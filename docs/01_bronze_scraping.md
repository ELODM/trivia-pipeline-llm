# Phase 1 — Couche Bronze : collecte des questions OpenTDB

Script : `src/ingestion/scrape_opentdb.py`

---

## 1. Objectif

Récupérer **l'intégralité** des questions de culture générale vérifiées de l'Open Trivia Database (OpenTDB), sans doublon et sans perte, et les stocker dans la couche bronze du data lake.

C'est le « sujet d'examen avec son corrigé » du benchmark : chaque question est fournie avec sa bonne réponse, ses mauvaises réponses, sa catégorie et sa difficulté. C'est ce qui permettra ensuite de noter objectivement les réponses des modèles d'IA.

---

## 2. La source : l'API OpenTDB

OpenTDB propose une API JSON gratuite, **sans clé d'API**. On construit simplement des URL :

| Endpoint | Rôle |
|---|---|
| `https://opentdb.com/api_category.php` | liste des catégories et de leurs identifiants |
| `https://opentdb.com/api_count_global.php` | nombre de questions vérifiées, au total et par catégorie |
| `https://opentdb.com/api_token.php?command=request` | obtention d'un token de session |
| `https://opentdb.com/api.php?amount=N&category=X&token=T` | récupération des questions |

Exemple de question renvoyée :

```json
{
  "type": "multiple",
  "difficulty": "medium",
  "category": "Entertainment: Film",
  "question": "Who directed \"Jaws\"?",
  "correct_answer": "Steven Spielberg",
  "incorrect_answers": ["George Lucas", "James Cameron", "Ridley Scott"]
}
```

### Les contraintes de l'API

| Contrainte | Conséquence |
|---|---|
| 50 questions maximum par appel | il faut boucler, catégorie par catégorie |
| 1 appel toutes les 5 secondes par adresse IP | au-delà, l'API refuse (code 5 ou HTTP 429) |
| Sans token, les questions sont tirées au hasard | doublons et impossibilité de savoir si on a tout |
| Le token expire après 6 heures d'inactivité | il faut savoir en redemander un |
| Seules les questions **vérifiées** sont distribuées | la référence est le nombre de questions vérifiées |

### Les codes de réponse

| Code | Signification |
|---|---|
| 0 | succès |
| 1 | pas assez de questions pour la demande |
| 2 | paramètre invalide |
| 3 | token introuvable ou expiré |
| 4 | token épuisé : toutes les questions disponibles ont été servies |
| 5 | limite de débit dépassée |

---

## 3. Le problème central : le token de session

Le token est la « clé » qui garantit l'unicité : l'API mémorise les questions déjà envoyées à ce token et ne les renvoie plus jamais.

Mais il crée un risque, souligné en cours :

1. on demande 50 questions avec le token ;
2. l'API les envoie et les marque comme « déjà servies » ;
3. le script plante avant de les avoir sauvegardées (coupure réseau, erreur…) ;
4. **ces 50 questions sont définitivement perdues pour ce token**, et le dataset est incomplet sans qu'on le sache.

Toute la conception du script répond à ce risque.

---

## 4. Les solutions mises en place

Les solutions s'inspirent des bonnes pratiques d'ingestion d'API et du projet open source [OTDB-Source (fork PortaPak)](https://github.com/MrSossidge/OTDB-Source), dédié au téléchargement complet et fiable d'OpenTDB.

| # | Principe | Problème résolu | Dans le script |
|---|---|---|---|
| 1 | **Brut immuable** | pouvoir rejouer et auditer | chaque réponse de l'API est écrite telle quelle dans son propre fichier, jamais modifié |
| 2 | **Écrire avant d'avancer** | perte de questions en cas de plantage | le lot est enregistré sur disque **avant** la mise à jour de l'avancement |
| 3 | **Écriture atomique** | fichier à moitié écrit si plantage pendant l'écriture | écriture dans un fichier temporaire puis renommage (`atomic_write_json`) |
| 4 | **Reprise** | devoir tout recommencer après un arrêt | le token est conservé dans `state.json`, et la liste des questions possédées est reconstruite depuis les fichiers bruts (`rebuild_index`) |
| 5 | **Limiteur de débit** | dépasser 1 requête / 5 s | le script mesure le temps écoulé depuis le dernier appel et n'attend que le complément (`_throttle`) |
| 6 | **Backoff exponentiel + jitter** | planter sur une erreur passagère | nouvel essai après 5 s, 10 s, 20 s… avec un léger hasard, 5 essais maximum, respect de l'en-tête `Retry-After` |
| 7 | **Erreurs temporaires vs permanentes** | insister inutilement | réseau, HTTP 429/5xx, JSON illisible, code 5 : on réessaie. Code 2, HTTP 4xx : la catégorie est marquée en échec |
| 8 | **Taille de lot décroissante** | perdre la fin d'une catégorie | en fin de catégorie, on demande 50, puis 25, 10, 5, 1 |
| 9 | **Garde-fous** | boucle infinie | plafonds de requêtes par catégorie, de lots sans nouveauté et de renouvellements de token |
| 10 | **Renouvellement du token** | token expiré (code 3) | nouveau token, les questions déjà possédées sont reconnues et ignorées |
| 11 | **Encodage propre** | textes mal décodés (`&quot;`, double échappement) | l'API est appelée avec `encode=url3986`, puis le texte est décodé |
| 12 | **Identifiant stable** | dédoublonnage fiable | `question_id` = empreinte (hash SHA-1) de catégorie + question + bonne réponse |
| 13 | **Réconciliation** | vérifier qu'on a tout | comparaison automatique avec le nombre officiel de questions vérifiées, catégorie par catégorie |
| 14 | **Journalisation** | savoir ce qui s'est passé | tout est affiché à l'écran et écrit dans `scrape.log` |

---

## 5. Fonctionnement du script, étape par étape

### Étape 1 — Démarrage et reprise
- `load_state()` lit `state.json` s'il existe (token et avancement d'une exécution précédente).
- `rebuild_index()` relit tous les fichiers bruts déjà présents et reconstruit la liste des questions possédées. C'est la **source de vérité** : on ne se fie pas uniquement au fichier d'état.

### Étape 2 — Récupération du référentiel
- Le script récupère la liste des catégories et le nombre de questions vérifiées attendu par catégorie.
- Ces deux réponses sont sauvegardées, horodatées, dans `raw/reference/` : on sait toujours à quel référentiel les résultats ont été comparés.
- Si aucun token n'est enregistré, il en demande un.

### Étape 3 — Collecte catégorie par catégorie (`download_category`)
Pour chaque catégorie :
1. si elle est déjà complète, elle est ignorée ;
2. sinon, le script demande un lot de questions (50, ou le nombre restant) ;
3. **le lot est immédiatement écrit** dans `raw/category_XX/batch_NNNNN.json` ;
4. les nouvelles questions sont comptées, les doublons ignorés ;
5. l'avancement est enregistré dans `state.json` ;
6. la boucle s'arrête quand le nombre attendu est atteint, ou quand l'API indique que la catégorie est épuisée, ou quand un garde-fou se déclenche.

Chaque catégorie reçoit un statut : `complete`, `incomplete` ou `failed`, avec la raison.

### Étape 4 — Construction du CSV bronze (`build`)
- Tous les fichiers bruts sont relus et décodés.
- Les doublons sont supprimés sur `question_id`.
- Le fichier `questions_raw.csv` est écrit de façon atomique.

### Étape 5 — Contrôle qualité
- Le nombre de questions uniques est comparé au référentiel de l'API.
- Le résultat est écrit dans `scrape_report.json`, et les catégories en écart sont signalées à l'écran.

---

## 6. Les fichiers produits

```
data/bronze/
├── raw/                                   ← source de vérité, jamais modifiée
│   ├── reference/
│   │   ├── categories_<date>.json         ← liste des catégories au moment de la collecte
│   │   └── count_global_<date>.json       ← nombres officiels au moment de la collecte
│   ├── category_09/
│   │   ├── batch_00001.json               ← réponse brute de l'API, avec date et paramètres
│   │   └── ...
│   └── category_XX/ ...
├── questions_raw.csv                      ← livrable bronze
├── scrape_report.json                     ← réconciliation attendu / collecté
├── state.json                             ← token et avancement (reprise)
└── scrape.log                             ← journal d'exécution
```

Le token n'est jamais écrit dans les fichiers bruts.

### Colonnes de `questions_raw.csv`

| Colonne | Description |
|---|---|
| `question_id` | identifiant unique et stable de la question |
| `category_id` | identifiant de la catégorie OpenTDB |
| `category` | nom de la catégorie (ex. `Entertainment: Film`) |
| `type` | `multiple` (QCM à 4 choix) ou `boolean` (vrai/faux) |
| `difficulty` | `easy`, `medium` ou `hard` |
| `question` | texte de la question |
| `correct_answer` | bonne réponse |
| `incorrect_answers` | mauvaises réponses, sous forme de liste JSON |
| `fetched_at` | date et heure de récupération (UTC) |
| `source_file` | fichier brut d'origine (traçabilité) |

### Pourquoi ce CSV reste du « bronze »

Il ne subit que deux opérations **techniques** :
- le décodage URL, qui retire un encodage de transport et ne modifie pas la donnée ;
- la suppression des doublons exacts.

Aucun nettoyage métier n'est fait ici (normalisation, découpage des catégories, etc.) : c'est le rôle de la couche silver.

---

## 7. Exécution

Depuis la racine du projet, avec l'environnement virtuel activé :

```powershell
python src/ingestion/scrape_opentdb.py
```

Durée : environ **15 minutes** (une centaine de requêtes espacées de 5 secondes).

| Situation | Que faire |
|---|---|
| Le script s'arrête (coupure internet, veille, `Ctrl+C`) | relancer la même commande : il reprend où il en était |
| Régénérer seulement le CSV, sans appeler l'API | `python src/ingestion/scrape_opentdb.py --build` |
| Repartir de zéro | supprimer `data/bronze/raw/` et `data/bronze/state.json` |

---

## 8. Résultats obtenus

| Indicateur | Valeur |
|---|---|
| Questions vérifiées annoncées par l'API | 5 299 |
| Questions uniques collectées | **5 298** |
| Doublons retirés | 1 |
| Catégories complètes | 24 / 24 |
| Durée de la collecte | environ 13 minutes |

### L'écart de 1 question

La catégorie *Science: Mathematics* (id 19) affiche 79 questions uniques pour 80 annoncées. Ce n'est pas une perte : la base OpenTDB contient **la même question en double** (même texte, même réponse). L'API la compte deux fois, le script a récupéré les deux exemplaires, a reconnu le doublon et n'en a conservé qu'un.

Ce chiffre de 5 298 correspond d'ailleurs au total obtenu par le projet open source OTDB-Source.

### Incidents absorbés pendant la collecte

Plusieurs erreurs `HTTP 429` (API trop sollicitée) et `ConnectionError` (coupures réseau) se sont produites. Toutes ont été résolues dès le premier nouvel essai grâce au backoff, **sans aucune perte de données** : chaque catégorie a atteint son nombre attendu.

### Comparaison avec la page d'accueil

Nombre affiché sur la page d'accueil d'opentdb.com le jour de la collecte : **_à compléter_**.

Si ce chiffre diffère, c'est que la page d'accueil ne compte pas la même chose que l'API : l'API ne distribue que les questions vérifiées, et le nombre de questions évolue au fil des validations.

---

## 9. Limites et améliorations possibles

- Le script s'exécute sur une seule machine et une seule IP : la limite de 5 secondes impose une durée incompressible d'environ 15 minutes.
- Pas de tests automatiques simulant l'API (le projet OTDB-Source en propose).
- Une collecte incrémentale (récupérer seulement les nouvelles questions validées depuis la dernière exécution) pourrait être ajoutée.
- Dans un contexte industriel, ce script serait une tâche d'un DAG Airflow, planifiée et supervisée.

---

## 10. Les points clés à retenir

1. Le token garantit l'unicité, mais une question servie et non sauvegardée est perdue : d'où le principe **« écrire avant d'avancer »** avec un stockage brut immuable.
2. Le script est **reprenable** : l'état est reconstruit à partir des fichiers bruts.
3. Le client respecte le débit de l'API et distingue erreurs **temporaires** (réessayées avec backoff) et **permanentes**.
4. Le résultat est **réconcilié** avec le référentiel de l'API, et chaque écart est expliqué.