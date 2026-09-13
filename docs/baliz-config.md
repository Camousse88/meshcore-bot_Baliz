# Profil de configuration Baliz

`config.ini.baliz` reprend les valeurs du fichier actif
`/etc/meshcore-bot/config.ini` du LXC 103, relevées le 13 septembre 2026,
et les adapte aux commandes de la branche `baliz`. Les valeurs ont été
revérifiées dans la console Proxmox du 103 via Safari le même jour, après
le redémarrage du service à 21:37:48 CEST.
Ce profil est propre au fork Baliz. Il ne remplace pas `config.ini.example`.

## Identité et fonctionnement conservés

- Nom `[BOT] Baliz`, français, fuseau `Europe/Paris` et coordonnées de Morlaix.
- Canaux surveillés : `#bzh-bot`, `Aar29`, `#bretagne` ; réponses aux DM et
  limite de 7 sauts conservées. Ce sont les noms des canaux déjà présents
  sur le companion : le profil ne crée pas les canaux et ne fournit pas leurs clés.
- Prompt système LLM intégral, sans reformulation, modèle `gemma3:4b`, endpoint
  `http://192.168.0.104:11434/v1/chat/completions`, budget de 100 tokens et
  délai de 90 secondes. Historique de 600 secondes / 5 tours, contexte réseau,
  contacts et météo conservés, ainsi que le seuil thermique de 85 °C.
- Port série par identifiant USB, chemins de données et de logs, limites de
  débit, réglages Web, horloge et états activés/désactivés des services conservés.
  Le rayon du tableau de synchronisation d'horloge est de 5 sauts
  (`Clock_Sync_Admin.dashboard_hop_radius`).

## Adaptation au routeur

`[Ask_Command]` active `ask` avec l'alias `baliz`, quatre messages maximum par
réponse et un délai global de 120 secondes. Les routes actives sont `mesh,wiki,llm`.
`[Mesh_Command]` active l'interrogation réseau/SQL et son accès direct.
L'accès direct `llm` reste disponible (`public_enabled = true`).

`test` et `path` sont désactivés dans le fichier du 103 : le profil conserve ce
choix et ne les annonce pas comme routes actives. Pour les utiliser derrière
`baliz`, activer leurs sections et ajouter `test,path` à `enabled_routes`.

## Wiki MeshCore Bretagne

Le RAG est un ajout au profil du 103 : sa configuration source n'en contient pas.
Le profil utilise le collecteur universel de la branche `baliz`, sans embeddings
ni base vectorielle, avec `https://wiki.meshcore.bzh` et la route `wiki`.
La locale Wiki.js est **`en`**, bien que les textes soient français : une requête
avec `fr` ne renvoie aucune page sur cette instance.

Le corpus public comprend `configuration`, `démarrer` et `ressources`, avec leurs
descendants. La collecte réelle a réussi : **12 pages, 117 sections**.
`materiel` est temporairement exclu : ses trois sources renvoient HTTP 403 en mode
invité, ce qui fait échouer la collecte complète. Pour l'inclure, autoriser
`read:pages` et `read:source` sur ces chemins pour Guest, ou fournir une clé via
`MESHCORE_WIKI_API_KEY`, puis ajouter `materiel` à `wiki_allowed_paths` et vérifier
la collecte. Aucune clé n'est enregistrée dans le profil.

L'index `data/wiki_rag/meshcore_bzh_wiki_pages.jsonl` est rafraîchi à la demande
lorsqu'il manque ou dépasse 24 heures. TLS reste vérifié. Le moteur sélectionne
au plus deux extraits de 1 400 caractères, pour un contexte total de 2 400
caractères, avec les seuils de pertinence natifs (`6` et `0.55`). Les réponses Wiki
utilisent le contexte documentaire isolé ; le prompt général Baliz est conservé.

Pour préconstruire l'index depuis le répertoire de travail du bot, avec son Python :

```bash
python scripts/wikijs_rag_collect.py \
  --base-url https://wiki.meshcore.bzh --locale en \
  --allow-prefix configuration --allow-prefix démarrer --allow-prefix ressources \
  --output data/wiki_rag/meshcore_bzh_wiki_pages.jsonl
```

Le service doit pouvoir écrire dans ce répertoire. Préconstruire l'index évite
que la première question attende la collecte. Voir [le fonctionnement du RAG](wiki-rag.md).

## Réglages exclus

- `[Bot] outgoing_flood_scope_override = fr-bre` : contournement local retiré.
  Aucun scope de sortie `fr` ou `fr-bre` n'est imposé par ce profil.
  Les paramètres natifs `PacketCapture.neighbors_scope_*`, inchangés par
  rapport à l'exemple d'origine, sont conservés ; la collecte est désactivée
  et `neighbors_self_scopes` est vide.
- `[Bot] device_autoadd_config = 0x07` : option du code local du 103, sans
  lecteur dans la branche `baliz`. Elle est omise plutôt que conservée comme
  réglage inopérant. `auto_manage_contacts = device` reste inchangé.

Les secrets et clés privées ne sont pas fournis. Les champs correspondants
sont vides dans la source ; les options MQTT contenant le mot `token` sont
des booléens ou des audiences publiques, pas des jetons d'authentification.
Les fichiers externes, bases de données, configuration des plugins locaux et
clés des canaux restent des dépendances de l'installation.

## Utilisation

Sauvegarder le fichier actif avant de copier le profil à son emplacement.
Sur le LXC 109, `botconfig` ouvre `/etc/baliz-test/config.ini`. La création de
ce profil et de sa PR ne déploie pas ce fichier dans un conteneur.

Avant démarrage sur une autre instance, adapter le port série, les chemins
`db_path`, `log_file`, `local_dir_path` et l'adresse d'Ollama. Le profil conserve
le Web Viewer du 103 sur `0.0.0.0:8080`, WebSocket activé, sans mot de passe
configuré : son accès dépend de la protection réseau/proxy de l'installation.
Le profil conserve aussi `auto_update_device_name = true` et le nom du bot.

## Vérification de la source

Le fichier d'exemple utilisé par le 103 correspond exactement à
`cc88e31907da8f313a7fe5490dcbbac5ab898ced:config.ini.example`.
Les 72 différences de valeurs ont été relevées ; aucune option de l'exemple
n'était absente. La reconstruction des 77 sections a été comparée à la source
avant les adaptations ci-dessus, avec les empreintes SHA-256 suivantes :

```text
Configuration : df40dea26bb1ee71aa790775b0ae9eaa5d74eed3a778ffd12538946d4dd5f84f
Llm_Command   : fee73c8cda24e083f71cb4df29fbf9862f6cd0e47e1cfbbf2d948bc9d01f2bb5
```

Calcul : sérialisation JSON de `{section: {clé: valeur}}` (ou de la section
LLM seule), `sort_keys=True`, `ensure_ascii=True`, encodée en UTF-8.
Ces empreintes portent sur les valeurs résolues, pas sur les commentaires
ou la présentation du fichier INI.

La validation INI de la branche ne signale aucune erreur. Le schéma actuel
émet trois avertissements de métadonnées : `public_enabled` est étiqueté à tort
comme redondant dans `Mesh_Command` et `Llm_Command`, et `cpu_temp_threshold`
est absent de l'exemple. Ces trois clés sont effectivement lues par le code
et sont conservées. La validation sur macOS signale également les chemins
Linux de l'installation, qui doivent être adaptés lors du déploiement.
