# Acceptance: a plain board that keeps the code door (PLAN.md §71)

The switch at `/admin/workshop/plugin` has three states: **Workshop**, **CTF with codes**, **Off**.
"CTF with codes" is CTFd's own board, with the token door and the cohorts in front of it.

## Run it

On a **fresh local instance**, never on one in use: the script flips the switch and the challenge
visibility (it puts both back).

    node scripts/board_codes_acceptance_check.js <base-url> <admin-pass> [path-to-playwright] [screenshot-dir]

    # on the debian machine
    NODE_PATH=/home/kc/CTFd/standalone-lab/node/node_modules \
      node scripts/board_codes_acceptance_check.js http://127.0.0.1:8085 admin playwright /tmp/shots

It prints `[ok]` / `[FAIL]` lines and ends with `ALL GREEN` or `FAILURES: [...]`. It makes two
codes and two accounts and deletes them.

## What to look for in each picture

| Picture | What it must show |
|---|---|
| `1-switch.png` | The switch page. Heading badge "CTF with codes", three radio buttons, the middle one ticked. The admin menu "Workshop" holds only the switch and the codes. |
| `2-door.png` | `/login` for a stranger: the token door, in French, one box ("Ton code"), no name, no password. |
| `3-board.png` | Where the participant lands after the code: CTFd's `/challenges` board, signed in under a made-up name, no workshop link in the bar. |
| `4-codes.png` | The codes page: the two test codes, each with its campus and date, each with one account, its own. |
| `5-waiting.png` | Challenges closed to participants: « L'événement n'est pas encore ouvert », « Tu es connecté. Reviens quand il sera ouvert. » No word about a workshop. |
| `6-off.png` | Switch on Off: `/login` is CTFd's own form again (name and password), no code box. |

## By hand, in five minutes

1. Admin: `/admin/workshop/plugin`, pick **CTF with codes**, Save.
2. Admin: Config, Visibility: Registration **private**, Challenges **private**.
3. Admin: `/admin/workshop/codes`, make a code. Make challenges the usual CTFd way.
4. Private window: open `/`, type the code, keep the personal code, tick, Continuer. You are on
   the board. Solve a challenge.
5. Another private window: open `/`, type the personal code. Same account, same score.
6. `/workshop` and `/supervisor/join` answer 404. The scoreboard shows your cohort only.
