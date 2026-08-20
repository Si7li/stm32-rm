# Structure de chunking des errata STM32 — approche et corrections

Ce document explique comment la structure de chunking des errata sheets STM32
(JSON `output/**/esXXXX_errata_rag.json`) a été définie, pourquoi elle a cette
forme, et quelles corrections ont été apportées pour la stabiliser.

---

## 1. Contexte : un RAG interne en boîte noire

Le RAG cible est une boîte noire : aucun contrôle sur son moteur. Le seul
contrat documenté est minimal :

- chaque chunk du JSON est indexé **atomiquement** sur son champ `embed_text` ;
- le RAG retourne des chunks (avec leur `citation`) en réponse à une requête.

Toute la logique structurée — filtrage pré-embedding, lookup par `errata_id`,
expansion parent/groupe, citation — vit donc **hors du RAG**, dans
`rag_utils.py` (`RAGIndex`). Aucune hypothèse n'est faite sur la compréhension
de `document_id` / `parent_document_id` par le moteur : le RAG ne fait que du
top-k vectoriel sur `embed_text`, et c'est le schéma JSON qui porte le reste.

Conséquence directe : **la structure du JSON est le contrat** entre l'extraction
et la consommation. Elle doit être déterministe, vérifiable et stable — d'où
l'architecture décrite ci-dessous.

---

## 2. Approche de chunking : un schéma canonique

Le découpage n'est pas un découpage naïf par longueur de tokens : il suit la
**structure sémantique du document source** (template ST des errata sheets) et
les **types de questions** auxquelles le RAG doit répondre.

### 2.1 Règle d'or : 4 chunks par errata + 1 groupe + 1 document

| section_type | parent | contenu |
|---|---|---|
| `full_entry` | None | titre + Description + Workaround |
| `description` | full_entry | texte Description uniquement |
| `workaround` | full_entry | texte Workaround uniquement |
| `applicability` | full_entry | matrice de statuts par révision (prose) |
| `group` | None | titre du groupe 2.x + liste de ses errata (ids + titres) |
| `document_summary` | None | méta doc (family, versions, total_errata, RM) + liste groupes |

Invariant structurel :

```
total_chunks == 4 * total_errata + total_groups + 1
```

Chaque errata est représenté par **exactement 4 chunks** (parent + 3 enfants).
Cette redondance est volontaire : elle permet au RAG de matcher la requête sur
le chunk le plus pertinent (`workaround` pour "quel workaround ?",
`applicability` pour "cette révision est-elle affectée ?") tout en garantissant
que l'expansion ramène toujours la chaîne complète au LLM.

### 2.2 Pourquoi 4 chunks et pas 1 ?

- **Précision du filtrage** : une question de type A ("quel workaround ?") filtre
  `filters.section_type = workaround` avant l'embedding, sans dépendre du rang
  vectoriel.
- **Sélectivité** : l'index peut écarter les chunks non pertinents d'un errata
  (ex. ne pas embarquer la Description quand on cherche le workaround).
- **Perte nulle** : l'expansion ramène toujours la chaîne complète (4 chunks +
  chunk groupe) vers le LLM — le découpage ne perd jamais d'information.

### 2.3 Mécanismes de liaison (2 clés indépendantes)

- **`document_id`** : `sha1(f"{DOC_ID}:{section_id}:{section_type}")` —
  déterministe, unique, dérivable de n'importe où sans lire le fichier.
- **`parent_document_id`** : relie chaque chunk enfant (`description`,
  `workaround`, `applicability`) à son `full_entry` ; `null` pour les chunks
  top-level (`full_entry`, `group`, `document_summary`).
- **`filters.group_id`** (string, ex. `2.8`) : relie tous les chunks d'un errata
  au chunk d'aperçu de son groupe.
- **`filters.errata_id`** : lookup exact par identifiant (`x.y.z`).

### 2.4 Enrichissement déterministe (aucun LLM)

Chaque chunk porte, en plus du texte, des métadonnées calculées par règles :
`conditions` (blocs de puces précédés de phrases déclencheuses), `impact_category`
(lexique ordonné), `keywords`/`aliases` (matching substring), `severity` (dérivé
de la matrice Table 3), `mentions_*` (booléens), `is_documentation_errata`
(section sans ligne de statut), statuts par révision dérivés de la matrice
(`affected_revisions`, `fixed_in_revision`, `has_workaround`,
`partial_workaround_only`).

### 2.5 La couche layout : une évidence, pas une source

Les tailles de police et le gras (extraits via pdfplumber) ne servent **jamais**
de source de texte : ils ne font que *confirmer* les headings repérés par regex
(ex. un sous-titre `Limitation` n'est reconnu que si la police le confirme).
La source reste `extract_text()` + normalisation `fix_tsu_dat`.

---

## 3. Les corrections apportées à la structure

### 3.1 Renommage du champ `chunks` → `documents`

**Problème** : le champ de liste `chunks` (wrapper) était **rejeté par
l'ingestion du RAG**. Chaque chunk est un item indexé — le vocabulaire standard
des ingestors RAG est `documents` (un chunk = un document indexé).

**Correction** :
- `chunks` → `documents` dans l'extracteur (`errata_extractor.py`), les
  consommateurs (`rag_utils.py`), les validateurs (`validate_json.py`,
  `regression_check.py`) et la référence `references/es0676_errata_rag.json`.
- Par cohérence de schéma, les champs d'identité ont été renommés :
  - `chunk_id` → `document_id`
  - `parent_id` → `parent_document_id` (il pointe vers un `document_id`)

**Point clé** : les **valeurs** sha1 n'ont pas changé — le hash est calculé sur
`f"{DOC_ID}:{section_id}:{section_type}"`, sans dépendre des noms de champs.
La migration n'a donc pas cassé les identifiants existants.

### 3.2 `FUNCTION_RE` : support des `_` dans les titres de groupe

**Problème** : la regex de détection des headings 2.x rejetait le caractère `_`.
Sur `es0639` (famille H5), les groupes `2.17 OTG_FS` et `2.18 OTG_HS` n'étaient
pas reconnus : les errata 2.17.x/2.18.x restaient rattachés au groupe précédent
`2.16 FDCAN` → la validation échouait sur la cohérence `peripheral`
groupe ↔ membres.

**Correction** : ajout de `_` à la classe de caractères de `FUNCTION_RE`.
Impact zéro sur les familles déjà traitées (classe élargie seulement).

### 3.3 Fallback `summary_start` pour les vieux sheets G0

**Problème** : `es0468` (STM32G070) et `es0547` (STM32G0B0) n'ont **pas la
légende** `Table 3. Summary of device limitations` — la matrice de statuts suit
directement la légende des statuts. Le repérage de `summary_start` échouait
(`ValueError`), le PDF n'était pas extrait.

**Correction** : dans `process_pdf`, en l'absence de la légende, fallback sur le
heading `^1\s+Summary of device errata`. La table doc-erratum (`Table 3.
Summary of device documentation errata`) ne déclenche pas ce fallback (texte
différent).

### 3.4 Pièges déjà documentés (rappels)

- Runs de sous-script pdfplumber (ex. `SU;DAT`) → normalisation `fix_tsu_dat`,
  garde-fou dans la validation.
- Workaround absent : fidélité au PDF (`None.` si écrit littéralement, `""`
  seulement si la section n'existe pas).
- Tables `Documentation erratum` (en-tête sur une seule rangée) vs Table 3
  (en-tête sur 2 rangées) — distinguées par le mot-clé dans l'en-tête.
- Une section de détail absente de Table 3 et de toute table doc-erratum
  (ex. es0661 2.6.2 RTC) est quand même extraite (matrice vide) mais déclenche
  un audit pour revue humaine.

---

## 4. Garanties : ce qui verrouille la structure

La structure est verrouillée par des portes **bloquantes** et des validations
**déterministes** :

1. **Portes structurelles** (`verify_extraction` + `verify_chunk_integrity`) :
   zéro section de détail sans errata, zéro id dupliqué, zéro errata orphelin,
   zéro groupe vide, exactement 1 `full_entry`/errata, `parent_document_id`
   résolus, `errata_ids` des chunks groupe existants. **FAIL → pas de JSON**.
2. **`validate_json.py`** : invariants (total_chunks, document_id uniques et
   déterministes, cohérence parent/enfant, cohérence statuts ↔ matrice,
   enrichissements traçables dans le texte, citation cohérente, tests de
   retrieval exacts) + **reproductibilité** (re-extraction byte-à-byte).
3. **`regression_check.py`** : baselines byte-à-byte, validation de la
   référence ES0676, résumé de couverture (4/4 par errata), checklist
   d'échantillonnage humain (`--seed`).
4. **`generate_report.py`** : agrégat `output/report.json` déterministe
   (deux exécutions → byte-identique).

Les dossiers de sortie par famille (`output/g0/`, `output/h5/`) sont lus par
les validateurs via `ERRATA_OUTPUT_DIR` (défaut `output/`).

---

## 5. Workflow de régénération

```powershell
# Extraction + validation d'une famille
python errata_extractor.py --input-dir input/Erratasheet/G0 --output output/g0 --validate
python errata_extractor.py --input-dir input/Erratasheet/H5 --output output/h5 --validate

# Validation complète (invariants + reproductibilité + retrieval exacts)
$env:ERRATA_OUTPUT_DIR="output/g0"; python validate_json.py
$env:ERRATA_OUTPUT_DIR="output/h5"; python validate_json.py

# Non-régression (baselines + référence + couverture + checklist humaine)
$env:ERRATA_OUTPUT_DIR="output/g0"; python regression_check.py
$env:ERRATA_OUTPUT_DIR="output/h5"; python regression_check.py

# Rapport agrégé
python generate_report.py
```

---

## 6. Résumé

La structure de chunking n'est pas un découpage générique : elle reflète la
sémantique du document (errata / groupe / document), elle est **déterministe**
(sha1 sur des clés stables, enrichissement sans LLM), **vérifiable** (portes
bloquantes + invariants + reproductibilité byte-à-byte) et **stable** (les
corrections n'ont jamais changé les valeurs d'identifiants, seulement la
forme du schéma pour satisfaire l'ingestion RAG).

Le contrat final côté RAG : une liste `documents`, chaque item indexé sur
`embed_text`, avec `document_id`/`parent_document_id` pour la liaison,
`filters` pour le filtrage pré-embedding et `citation` pour la source.
