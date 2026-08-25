# Créer son propre atelier

Guide pour écrire un atelier et le mettre en ligne. Il n'y a que trois choses à produire :
un dépôt de contenu (du Markdown), éventuellement un runtime (une application web), et une
instance CTFd.

La référence complète est `docs/CONTENT_CONVENTION.md`. Ce guide en est la version courte.


## 1. Le dépôt de contenu

Un sujet, c'est un dépôt git :

```
mon-sujet/
├── subject.yaml      # la fiche d'identité du sujet
├── intro.md          # le texte, autant de fichiers .md que voulu
├── chapitre1.md
├── img/              # les images, jamais de lien vers un autre site
└── quiz_answers.yaml # seulement s'il y a des quiz
```

Rien d'autre n'est obligatoire. Pas de sommaire à tenir à jour : la structure est lue
directement dans les `.md`.

### `subject.yaml`

```yaml
schema_version: "2.0"

project:
  name: "MiniASM"
  slug: "miniasm"
  summary: "L'ordinateur en papier, en vrai."
  entrypoint: "intro.md"

platform:
  mode_default: self_serve       # self_serve | instructor_led
  validation_default: checkpoint # checkpoint | flag | token
  points_default: 25

documents:                       # l'ordre de lecture
  - intro.md
  - chapitre1.md
```

Ajoutez le bloc `runtime:` seulement si l'atelier a une application web à côté du texte
(partie 4).


## 2. Écrire les `.md`

Le Markdown reste du Markdown normal, lisible sur GitHub. Tout ce que la plateforme a besoin
de savoir est mis dans des **commentaires HTML**, donc invisibles à la lecture.

Un commentaire `ws:` placé **juste après un titre** décrit ce titre.

### Un exercice

```markdown
## Faites bouger le pad dans l'autre direction
<!-- ws: {type: exercise, points: 50} -->

En vous inspirant du code précédent, faites en sorte que le pad puisse
bouger à droite comme à gauche.
```

Le texte qui suit, jusqu'au prochain titre marqué, appartient à cet exercice.

Champs utiles :

| Champ | À quoi ça sert |
|---|---|
| `type` | `exercise`, `chapter`, `prose`, `quiz`, `hint` |
| `id` | identifiant stable. Par défaut il est déduit du titre |
| `points` | points gagnés. Par défaut `points_default` |
| `validation` | `checkpoint`, `flag` ou `token`, si différent du défaut |
| `optional` | `true` pour un bonus : il ne bloque rien et ne compte pas dans la progression |
| `requires` | liste d'ids à finir avant celui-ci (rare, sert entre chapitres) |

### Les titres sans marqueur

Un titre sans commentaire `ws:` est de la simple prose. Il ne devient pas un exercice.

Un titre non marqué de niveau `#` ou `##` **ouvre une partie** : c'est le nom de section que
les participants voient. Si un titre de ce niveau n'est qu'une explication au milieu d'un
chapitre, dites-le, sinon il renommerait la partie sans le vouloir :

```markdown
## Notion d'objet en Lua
<!-- ws: {type: prose} -->
```

### Les chapitres et l'ordre

Par défaut les exercices s'enchaînent : chacun demande le précédent. Pour un chapitre où les
exercices sont indépendants et se font dans n'importe quel ordre :

```markdown
# Gameplay
<!-- ws: {type: chapter, topology: free} -->

Choisis un ou plusieurs défis, dans l'ordre que tu veux.
```

Un chapitre n'est jamais un exercice, c'est juste une portée. `topology: linear` est la valeur
par défaut et n'a pas besoin d'être écrite.

Attention : un chapitre `free` peut être fermé sans rien faire. Ne mettez pas en `free` un
chapitre obligatoire.

### Les indices

Un `<details>` normal, précédé d'un marqueur :

```markdown
<!-- ws: {type: hint, cost: 5} -->
<details><summary>Indice</summary>

La fonction `btn` prend un nombre en paramètre (3 : flèche droite).

</details>
```

`cost` est facultatif : c'est le prix de l'indice en points.

Rappel de fond : **ne donnez jamais la réponse**. Un indice oriente.

### Les quiz

```markdown
<!-- ws: {type: quiz, id: btn-doc} -->
> Pour lire les touches haut et bas du joueur 1, j'utilise (2 réponses)

* A. btn(0)
* B. btn(1)
* C. btn("up")
* D. btn("down")
```

`- A.` pour une seule bonne réponse, `* A.` pour plusieurs. Les bonnes réponses ne sont **pas**
dans le Markdown, elles vont dans `quiz_answers.yaml` :

```yaml
schema_version: "1.0"
answers:
  init-cmd: {answer: B}
  btn-doc: {answers: [A, B]}
  rebond: {pairs: {A: b, B: c, C: a}}
```

### Les images

Une image vit dans le dépôt et se référence en chemin relatif :

```markdown
![Le repère de la grille](img/origin.png)
```

Jamais une URL `https://raw.githubusercontent.com/...`. Les deux s'affichent pareil sur GitHub,
mais seul le chemin relatif survit à un renommage du dépôt et fonctionne dans une salle sans
internet.

### L'intro et la fin

Le premier document (`entrypoint`) devient la première étape, avec un bouton "j'ai lu". Le texte
qui suit le dernier exercice devient la dernière étape, qui demande un avis sur l'atelier.
Vous n'avez rien à faire pour ça, écrivez simplement une intro et une conclusion.


## 3. Comment une étape est validée

Trois modes, à choisir dans `validation_default` ou par exercice.

- **`checkpoint`** (défaut) : la plateforme génère un code par exercice et écrit la liste dans
  `instructor_codes.<sujet>.yaml`. Le formateur donne le code quand il a vu le travail. Idéal
  pour un atelier encadré.
- **`flag`** : la réponse est la solution elle-même, trouvée en faisant l'exercice. Les réponses
  vont dans un fichier `flags.yaml` à part, jamais dans le Markdown :

  ```yaml
  flags:
    003_arguments: "shell1{exemple}"
  ```

  Si le dépôt est public, chiffrez ce fichier ou fournissez-le au moment de la synchronisation.
- **`token`** : le runtime corrige lui-même et affiche un jeton quand les tests passent.
  L'exercice porte alors un `token_id` qui est son identifiant dans le runtime. Rien à écrire
  comme réponse : le jeton est différent sur chaque instance.


## 4. Le runtime (facultatif)

Le runtime, c'est l'application web affichée à côté des instructions : un éditeur, un émulateur,
un jeu. Si votre atelier n'en a pas besoin, sautez cette partie.

**On ne réécrit pas un runtime qui existe déjà.** S'il existe en application web, on le sert tel
quel.

### Ce qu'on attend de l'application

- Un site **statique** : HTML, CSS, JS, éventuellement du WASM. Pas de backend.
- Des URLs **relatives**, ou un build qui accepte une base (`VITE_BASE` pour Vite). L'app est
  servie sous `/runtime/<id>/<version>/`.
- Un dépôt git public (ou accessible) avec, si besoin, un script qui récupère ses dépendances.

L'application n'a **rien à modifier chez elle** pour parler à la plateforme.

### La brancher

1. Ajoutez un `case` dans `tools/build_runtime.sh` avec l'URL du dépôt, puis :

   ```sh
   tools/build_runtime.sh mon-runtime
   ```

   Ça produit `plugins/workshop/runtimes/mon-runtime/<sha>/` et affiche la version à déclarer.

2. Écrivez l'adaptateur `plugins/workshop/assets/runtime/adapters/mon-runtime.js`. C'est du JS
   injecté dans l'iframe par la plateforme. Le minimum, c'est d'annoncer qu'on est prêt.
   Copiez `adapters/mock.js`, il fait 45 lignes.

3. Déclarez-le dans `subject.yaml` :

   ```yaml
   runtime:
     engine: "TIC-80"
     language: "lua"
     id: "mon-runtime"
     version: "7e18bae"      # la version affichée par build_runtime.sh
     title: "Mon éditeur"
     pane:
       placement: side       # side | bottom
       open: false           # ouvert dès l'arrivée, ou on laisse lire d'abord
       size: 55              # pourcentage de la largeur
   ```

Le protocole complet (les messages échangés, la sauvegarde du travail en cours) est dans
`docs/RUNTIME_PROTOCOL.md`. Règle à retenir : **un runtime ne valide jamais une étape**, il
propose, le participant valide.


## 5. Plusieurs sujets dans un même atelier

Un dépôt de sujet est déployable tel quel. Si vous voulez composer plusieurs sujets, faites un
dépôt d'atelier avec un `workshop.yaml` :

```yaml
schema_version: "2.0"

workshop:
  name: "Discover Linux"
  slug: "discover-linux"
  summary: "Le terminal, d'abord en jeu puis pour de vrai."

subjects:
  - repo: mon-org/shell-rpg
    ref: v2026.01        # un tag ou un commit, jamais une branche
    role: starter
  - repo: mon-org/shell-1
    ref: 8f3c2d1
    role: advanced
    order: 1             # sans `order`, les sujets avancés sont au libre choix
```

Règle imposée : **le starter est toujours à finir avant les sujets avancés**. La plateforme pose
les prérequis toute seule.


## 6. Créer l'instance CTFd

### Pour tester en local

```bash
docker compose up -d                      # CTFd sur http://localhost:8080
# faire l'assistant d'installation dans le navigateur, puis :
python3 tools/sync_subject.py chemin/vers/mon-sujet \
    --url http://localhost:8080 --admin-user admin --admin-pass '...'
```

Pour un atelier multi-sujets, `tools/sync_workshop.py` à la place, avec le dossier qui contient
`workshop.yaml`.

Relancer la commande met le contenu à jour : les étapes sont réconciliées par leur `id`, les
réussites des participants sont conservées.

### Pour de vrai

Une instance CTFd par atelier, ou mieux, par **session**. Déclarez-la dans
`deploy/instances.yaml` :

```yaml
domain: mondomaine.fr

instances:
  - name: mon-sujet
    port: 9085              # port en 908x, sur la boucle locale uniquement
    content: content/mon-sujet
```

Puis, sur le serveur :

```bash
python3 tools/provision.py render      # fichiers .env et vhosts nginx
python3 tools/provision.py up      mon-sujet
python3 tools/provision.py setup   mon-sujet   # assistant CTFd + réglages
python3 tools/provision.py runtimes mon-sujet  # construit les runtimes utilisés
python3 tools/provision.py sync    mon-sujet   # importe le contenu
python3 tools/provision.py secrets mon-sujet   # mot de passe admin, code d'inscription
```

Les identifiants sont générés dans `deploy/secrets.yaml`, qui n'est pas versionné et n'existe
qu'en un seul exemplaire. **Sauvegardez-le.**

Le certificat TLS, nginx et le DNS sont détaillés dans `docs/DEPLOY.md`. HTTPS n'est pas
optionnel : certains runtimes ne fonctionnent pas hors contexte sécurisé.


## 7. Avant de livrer

- Le contenu de l'atelier s'écrit **en français**, en phrases simples.
- Les textes de l'interface de la plateforme restent en anglais, ce n'est pas votre problème
  ici.
- Ne donnez jamais la réponse, ni dans le texte ni dans les indices.
- Vérifiez que chaque `id` cité dans un `requires` existe, et que chaque image référencée est
  bien dans le dépôt. Le linter refuse le contraire.
- Pour une session, épinglez un tag ou un commit, jamais une branche : une session doit être
  reproductible.
