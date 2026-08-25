#!/bin/sh
# The fallback chain of the mirroring cache (PLAN.md §18.8), end to end.
#
#     sh scripts/mirror_check.sh
#
# Everything here is nginx, curl and docker: the "CDN" and the "GitHub release"
# are nginx containers, and an outage is `docker stop`. Set it up first:
#
#   docker network create vmtest
#   docker run -d --name cdn --network vmtest -v $PWD/cdn.conf:/etc/nginx/conf.d/default.conf:ro …
#   docker run -d --name gh  --network vmtest -v $PWD/gh.conf:/etc/nginx/conf.d/default.conf:ro …
#   docker run -d --name mirror-test --network vmtest -p 8090:80 \
#     -e MIRROR_UPSTREAM_1=http://cdn -e MIRROR_UPSTREAM_2=http://gh \
#     -e MIRROR_UPSTREAM_3=http://127.0.0.1:9 -e MIRROR_UPSTREAM_4=http://127.0.0.1:9 \
#     -e MIRROR_CACHE_SIZE=1g -e MIRROR_MAX_AGE=604800 -e "MIRROR_RESOLVER=127.0.0.11" \
#     -v $PWD/compose/mirror/default.conf.template:/etc/nginx/templates/default.conf.template:ro \
#     nginx:1.27-alpine
#
# It is outside scripts/phase2_validate.py on purpose: that suite tests a CTFd
# instance, and this tests a service that does not know CTFd exists.
set -u
BASE=http://localhost:8090
FILE=/cdn.bin
fails=0

check() {
  if [ "$2" = "$3" ]; then
    echo "  [ok] $1"
  else
    echo "  [FAIL] $1 (want '$3', got '$2')"
    fails=$((fails + 1))
  fi
}

hdr() { curl -s -D- -o /dev/null "$BASE$FILE" | tr -d '\r' | grep -i "^$1:" | tail -1 | cut -d' ' -f2-; }
body() { curl -s "$BASE$FILE" | head -c 15; }
flush() { docker exec mirror-test sh -c 'rm -rf /var/cache/mirror/*' >/dev/null 2>&1; }

echo "== upstream 1 answers =="
docker start cdn gh >/dev/null 2>&1; sleep 2; flush
check "served by the first upstream" "$(hdr x-mirror-source)" "1"
check "its body comes through" "$(body)" "BUNDLE-FROM-CDN"
check "the next request is a cache hit" "$(hdr x-mirror-cache)" "HIT"
check "ranges are offered" "$(hdr accept-ranges)" "bytes"
check "another origin may fetch it" "$(hdr access-control-allow-origin)" "*"
check "browsers are told it is immutable" "$(hdr cache-control)" "public, max-age=604800, immutable"

echo "== upstream 1 is down: the next one takes over, redirect and all =="
docker stop cdn >/dev/null 2>&1; flush
check "served by the second upstream" "$(hdr x-mirror-source)" "2-redirect"
check "its body comes through" "$(body)" "BUNDLE-FROM-GIT"

echo "== every upstream is down: the local copy saves the session =="
docker stop gh >/dev/null 2>&1; flush
docker exec mirror-test sh -c "mkdir -p /srv/mirror && printf BUNDLE-FROM-LOCAL > /srv/mirror/cdn.bin"
check "served from the local copy" "$(hdr x-mirror-source)" "local"
check "its body comes through" "$(body)" "BUNDLE-FROM-LOC"

echo "== nothing anywhere =="
docker exec mirror-test sh -c 'rm -f /srv/mirror/cdn.bin'
check "a missing resource is a plain 404" \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE$FILE)" "404"

echo "== a range request is served out of the cache =="
docker start cdn >/dev/null 2>&1; sleep 2; flush
curl -s -o /dev/null "$BASE$FILE"
docker stop cdn >/dev/null 2>&1
check "partial content, with every upstream now down" \
  "$(curl -s -o /dev/null -r 0-9 -w '%{http_code}' $BASE$FILE)" "206"
check "and the right ten bytes" "$(curl -s -r 0-9 $BASE$FILE)" "BUNDLE-FRO"

docker start cdn gh >/dev/null 2>&1
echo ""
[ "$fails" -eq 0 ] && echo "ALL GREEN" || echo "$fails FAILURE(S)"
exit "$fails"
