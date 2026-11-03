# Visibilité des messages de canal sur le web

Dans **Radio → Channel Management**, la case **Live Channel Messages** autorise
la publication des messages déchiffrés d'un canal dans la page **Real-time**.
Elle est désactivée par défaut, y compris à la création d'un canal.

Un canal masqué reste configuré et surveillé par Baliz. Ses messages restent
disponibles localement pour le traitement et les statistiques ; le filtrage porte
sur la diffusion web et l'historique rejoué aux navigateurs.

La visibilité est conservée dans la base et associée au nom et à la clé du canal,
indépendamment de son emplacement radio. Un changement de clé ou une ambiguïté
entre des canaux ne réutilise pas automatiquement une autorisation. L'ancien
historique sans identité de canal vérifiable n'est pas republié.

Les messages et commandes d'un canal masqué sont filtrés avant émission Socket.IO,
y compris lors du replay. Les données déchiffrées ajoutées aux paquets côté serveur
sont retirées du flux des paquets. Le décodeur de la page n'est configuré qu'avec
les noms des canaux explicitement visibles ; les clés privées ne sont jamais
retournées par les listes publiques.

## Administration et API

- `GET /api/channels` et `/api/channels/live` : liste des canaux visibles, sans clés.
- `GET /radio/api/channels` : gestion complète des canaux.
- `PUT /radio/api/channels/live-visibility` : booléen `live_messages_visible`, avec
  `channel_idx` et `channel_name` pour identifier le canal.
- `POST /radio/api/channels` : création avec `live_messages_visible=false` par défaut.
  L'ancienne API de création ne permet pas d'activer la publication.

Les routes de gestion sont protégées par l'authentification du Web Viewer lorsqu'elle
est configurée. En cas d'authentification au niveau du reverse proxy, protéger aussi
le préfixe `/radio/`, comme sur l'installation Baliz. La protection des pages et
API d'administration reste indispensable pour empêcher une modification non autorisée
et protéger les clés et journaux locaux.

La migration 33 ajoute une table indépendante : la synchronisation des canaux depuis
la radio n'efface pas les préférences d'affichage.
