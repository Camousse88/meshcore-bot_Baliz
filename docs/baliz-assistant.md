# Assistant Baliz : ask, mesh et llm

Cette architecture appartient pour le moment à la branche `baliz` du fork.
Aucune PR vers Tigro n'est nécessaire pour l'utiliser.

## Commandes et responsabilités

| Entrée | Fonction | Réglages |
| --- | --- | --- |
| `ask`, alias configurable `baliz` | Routeur de l'assistant | `Ask_Command` |
| `mesh`, `query`, `sql` | Questions libres sur les données réseau locales | `Mesh_Command` |
| `llm`, `ia`, `ai`, `chat` | Accès direct au moteur conversationnel existant | `Llm_Command` |
| `test`, `path` | Outils RF existants | Sections existantes |

`ask` ne génère plus de SQL. Les anciennes fonctions de `AskCommand` ont été
extraites dans `MeshService`. `LlmCommand` délègue à `LlmService` et ne contient
aucune règle de routage. Les deux services partagent le transport HTTP LLM.
La conversation ne génère ni n'exécute plus de suivi `[[SQL: ...]]` : une requête
sur la base doit passer par `mesh` (directement ou derrière `ask`).

## Routage

Le routeur est déterministe et indépendant des entrées/sorties. Il reconnaît des
formulations françaises et anglaises. Il n'utilise pas de classification LLM.

1. Route explicitement demandée (`ask mesh ...`, `ask wiki ...`, `ask llm ...`,
   `ask test`, `ask path`) ou aide.
2. Réception du message courant vers `test`.
3. Chemin du message courant vers `path`.
4. Intention documentaire explicite vers le Wiki.
5. Question sur les observations réseau vers `mesh`.
6. Recherche Wiki ; en l'absence de correspondance, conversation générale.

Une demande documentaire explicite sans source donne « Aucune source pertinente ».
Elle ne se transforme pas en requête réseau ou en réponse inventée par le modèle.
Une demande réseau n'interroge pas le Wiki. Les réponses documentaires excluent
contexte live et historique, et ne sont pas ajoutées à l'historique général.
`demain` seul ne déclenche aucun traitement météo. La météo n'est pas une route
spécialisée de cette première version ; le contexte météo existant du LLM reste
configurable. En cas de formulation ambiguë, utiliser une route explicite.

## Configuration et migration

```ini
[Ask_Command]
enabled = true
aliases = baliz
enabled_routes = test,path,mesh,wiki,llm
route_timeout_seconds = 120
max_pages = 4

[Mesh_Command]
enabled = true
public_enabled = true

[Llm_Command]
enabled = true
public_enabled = true
# Conserver ici endpoint, model, timeout_seconds et les options de contexte/RAG.
```

- Ajouter `aliases = baliz` dans `Ask_Command`. Retirer cet alias de
  `Llm_Command` s'il y figure, pour éviter deux commandes avec le même déclencheur.
- Reporter les restrictions de canaux de l'ancien `Ask_Command` dans
  `Mesh_Command` si elles concernaient l'interrogation réseau. Définir séparément
  les canaux autorisés pour l'assistant.
- `ask <question réseau>` fonctionne toujours via routage ; `ask tables` et
  `ask help` changent de sens. L'aide réseau est `mesh help`, le schéma `mesh tables`.
- Les alias `query` et `sql` appartiennent désormais à `mesh`.
- L'ancien réglage `Llm_Command.db_query_enabled` n'est plus utilisé. Activer
  `Mesh_Command.enabled` et la route `mesh` à sa place.
- `enabled` active la capacité. `public_enabled = false` masque seulement
  l'accès direct de `llm` ou `mesh`, sans empêcher `ask` de l'utiliser.
- La route `wiki` exige `Llm_Command.enabled` et la configuration RAG existante.
  Le RAG reste désactivé par défaut : configurer sa source avant utilisation.
- Les paramètres apparaissent dans l'éditeur des plugins. Les alias utilisent
  l'éditeur de mots-clés déjà existant.

Les autorisations de l'assistant **et** de la capacité cible s'appliquent.
Les restrictions de canaux et les cooldowns ne sont pas contournés. Un outil
indisponible produit une erreur explicite, sans basculer vers un autre outil.

## Organisation du code

| Fichier | Responsabilité |
| --- | --- |
| `modules/commands/ask_command.py` | Alias, réglages, appel du dispatcher et réponse |
| `modules/assistant/router.py` | Décisions pures : route, question, raison |
| `modules/assistant/dispatcher.py` | Autorisations, capacités, délai, erreurs |
| `modules/assistant/mesh_service.py` | SQL en lecture seule sur les six tables réseau autorisées |
| `modules/assistant/llm_service.py` | Moteur existant : contexte, Wiki, génération et historique |
| `modules/assistant/llm_client.py` | Transport HTTP commun |
| `modules/assistant/response.py` | Pagination en octets UTF-8 et envoi final |

L'adaptateur RF utilise exclusivement les commandes `test` et `path` distribuées.
Il copie le message réel, conserve son identité, sa portée régionale et ses
métadonnées RF, puis capture les réponses par le mécanisme existant `capture_sink`.
Il n'utilise pas le message synthétique du planificateur, ne remplace aucune
méthode d'envoi et refuse les plugins de remplacement non audités. L'assistant
émet ensuite la réponse capturée par le transport normal et ses limites RF.

L'interrogation SQL conserve la validation lecture seule, borne les résultats à
20 lignes, applique un contrôle SQLite des tables accessibles et une limite
CPU de deux secondes pour la requête. La protection thermique est appliquée aux
capacités LLM et mesh. Le timeout global coupe l'attente de l'assistant ; une
requête HTTP déjà lancée dans un thread peut finir en arrière-plan jusqu'à son
propre timeout, sans émettre de réponse radio.

## Validation sur le LXC 109

Ne pas démarrer de service radio pour ces vérifications. Utiliser le compte
`baliz-test`, le dépôt `/opt/baliz-test` et son environnement `.venv`.

```sh
.venv/bin/python -m pytest tests/test_assistant_router.py tests/test_assistant_integration.py tests/commands/test_llm_command.py tests/test_wiki_rag.py --no-cov
.venv/bin/python -m pytest tests -m 'not mqtt' --no-cov
.venv/bin/ruff check .
.venv/bin/mypy modules/
```

Le lanceur `scripts/baliz_assistant_smoke.py` teste les véritables commandes avec
une base réseau fictive, un index documentaire connu et, si demandé, Ollama.
Il capture toutes les réponses et n'initialise aucun companion radio.
Les tests RF utilisent des messages de test ; ils ne remplacent pas une recette
ultérieure avec de vrais paquets reçus.
