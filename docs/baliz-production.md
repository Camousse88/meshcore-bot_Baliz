# Baliz : production et branches

## Organisation

| Branche | Rôle |
| --- | --- |
| `baliz` | Dernière version publiée de Baliz, avec ses fonctions et particularités. Le commit effectivement déployé est indiqué dans le suivi de production ci-dessous. |
| `main` | Branche historique du fork, distincte de la production Baliz. Cette synchronisation ne la modifie pas. |
| `config/baliz-identity` | Travail dédié au profil public et à l'identité de Baliz. Cette synchronisation ne la modifie pas. |
| `sync/production-20261105` | Publication des changements déjà déployés le 5 novembre 2026, puis intégration dans `baliz`. |
| `sync/tigro-20261106` | Intégration de `Tigro14/meshcore-bot:main` après fusion de la PR #80. |
| `feat/*`, `fix/*`, `sync/tigro-*` | Développements et intégrations isolés, à relire avant intégration dans `baliz`. |

Les mises à jour de Tigro sont intégrées dans Baliz en conservant ses particularités. Publier une version de production ne remplace pas `main`, ne réécrit pas l'historique et ne supprime pas les branches de travail existantes.

## Synchronisation GitHub du 6 novembre 2026

La branche `baliz` intègre le `main` de Tigro au commit `d040c5ad94c03b29b29ade6001e1084264adc46f` (PR #80). Cette fusion conserve les fonctions propres à Baliz et rapproche les historiques des corrections DM et horloges déjà présentes dans les deux versions.

Les corrections de typage et d'isolation des tests de Tigro, la documentation des DM et horloges et l'exécution des tests JavaScript dans le workflow sont reprises. Les textes français de Baliz, ses symboles de configuration, ses filtres et ses analyses de carte sont conservés. Le LLM, le routage sémantique, les règles d'accès et le profil public ne changent pas.

Les nouveautés BBS, vigilance et indication de région dans Contacts présentes uniquement sur `dev` de Tigro ne font pas partie de cette fusion de `main`. Voir le [compte rendu de validation](validation/baliz-sync-20261106.md).

Cette synchronisation met à jour le dépôt GitHub ; elle ne déploie pas de nouvelle version sur le LXC103.

## Production du 5 novembre 2026

- Base GitHub : `72315457c6f97136a78b8cb29b5bbbcdbdc222bc`, intégration de Tigro du même jour.
- Source déployée sur le LXC103 : `a274f5a1625ab7b2776b110c1e2c425097a52808`.
- Tag de référence de cette source : `baliz-production-2026-11-05`.
- Les 12 commits suivants sont publiés avec leur historique d'origine. Les seuls ajouts de cette synchronisation sont cette documentation et son lien dans le README.
- Les empreintes de 295 fichiers applicatifs suivis (modules, point d'entrée et dépendances), dont les 25 fichiers applicatifs modifiés depuis la base, ont été comparées aux fichiers du LXC103 avant publication : aucune différence ni fichier manquant.
- Les anciennes notes de validation mentionnant une diffusion limitée au LXC décrivent l'autorisation initiale. La publication GitHub de cet ensemble a été autorisée ensuite, le 5 novembre 2026.

### Envois privés et assistant

La préparation d'un DM vérifie une identité unique dans la radio, peut restaurer un contact à partir de données complètes et distingue une absence d'émission d'une réception non confirmée ou confirmée. Le délai minimal d'attente d'un ACK passe à 30 secondes, et la synchronisation d'horloge conserve les réponses reçues pendant cette attente.

Seuls les expéditeurs identifiés comme compagnons peuvent déclencher le LLM. Les types inconnus, répéteurs et autres appareils ne déclenchent pas l'assistant. Le routage sémantique et les règles spécifiques de Baliz sont conservés.

Validation : [résolution et livraison des DM](validation/baliz-dm-resolution-20261105.md), [ACK et compagnons](validation/baliz-ack-companions-20261105.md).

### Horloges

Contacts et Time partagent les mêmes observations datées et les mêmes badges de dérive. La réception d'une commande ne vaut pas confirmation d'une horloge réglée. La dérive mesurée reste visible indépendamment du statut. Le bouton Auto clkreboot enregistre correctement le choix de chaque cible.

Validation : [observations et statut](validation/baliz-clock-evidence-20261105.md), [dérive affichée](validation/baliz-clock-drift-display-20261105.md), [Auto clkreboot](validation/baliz-auto-clkreboot-toggle-20261105.md), [badges communs](validation/baliz-shared-clock-badges-20261105.md).

### Carte et téléphone

La carte indique les dérives par un symbole d'horloge distinct des avertissements de configuration. Les avertissements restent au premier plan et les fiches contenant beaucoup d'informations s'adaptent à l'espace disponible. Sur téléphone, les cibles Time s'affichent en cartes lisibles ; le bloc de configuration reste directement visible, comme demandé.

Validation : [carte et fiches](validation/baliz-mesh-clock-markers-20261105.md), [Time sur téléphone](validation/baliz-time-mobile-20261105.md).

## Vérifications et limites

Les contrôles ciblés Python 3.10/3.13, JavaScript et les vérifications visuelles sont détaillés dans les notes ci-dessus. Aucun message radio de test n'a été envoyé par ces contrôles.

Le dernier bilan complet, réalisé sur la base d'intégration de Tigro, était de 6 754 tests réussis, 44 échecs préexistants, 17 ignorés et 8 exclus, sans nouvel échec par rapport à sa référence. Les corrections de cette publication ont ensuite été vérifiées par des sélections ciblées ; ce bilan complet ne constitue donc pas un résultat de suite complète sur le présent commit. Les tests hérités encore en échec ne sont pas présentés comme réussis.

Le workflow GitHub de tests existant cible `main`, `master` et `dev`. Une PR vers `baliz` ne le déclenche pas automatiquement. Les résultats locaux documentés restent distincts d'un résultat GitHub Actions.

## Configuration et données locales

Le dépôt conserve le profil public `config.ini.baliz`. Le fichier actif de production, les secrets, les bases de données, les messages privés et les préférences d'affichage des canaux restent sur le serveur. Le profil public n'est pas une copie à appliquer sur la configuration active.

Cette publication ne déploie aucun changement supplémentaire dans le LXC103 et ne modifie aucun réglage en production.
