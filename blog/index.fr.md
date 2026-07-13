---
title: "Construire un historique long terme des bougies Hyperliquid à partir d'une courte fenêtre REST"
description: "Un pipeline Python et ClickHouse résistant aux redémarrages pour accumuler les bougies perpétuelles Hyperliquid à une minute, réparer les trous récents et surveiller le moment où la récupération REST n'est plus possible."
date: 2026-07-13
image: images/cover.png
categories: ["Data Science", "Capital Markets", "Quantitative Research"]
---

# Construire un historique long terme des bougies Hyperliquid à partir d'une courte fenêtre REST

L'interface de programmation REST (API) d'Hyperliquid ne donne accès qu'à une tranche récente des bougies à une minute. Sa documentation officielle annonce la disponibilité des 5 000 bougies les plus récentes. Ce projet retient donc 5 000 créneaux d'une minute comme fenêtre de requête prudente. Si chaque minute contient une bougie, ces 5 000 ouvertures inclusives couvrent 4 999 minutes, soit environ 3 jours et 11 heures. Une fois l'historique de la source dépassé, REST ne peut plus reconstruire une panne.

Cette contrainte change la nature du problème. Il ne s'agit pas d'un simple téléchargement ponctuel. Le service doit tourner en continu, savoir précisément où s'arrête le stockage pour chaque contrat perpétuel, tolérer les insertions répétées et réparer les trous tant que la source les conserve encore.

Le pipeline reste volontairement ciblé : découvrir les marchés perpétuels actifs, récupérer les bougies fermées à une minute, les écrire dans ClickHouse et enregistrer assez d'informations de qualité pour détecter rapidement un incident. Il ne prend aucune position, ne consomme pas de flux WebSocket, n'ingère pas d'archive profonde et n'administre pas la base de données.

## Le temps s'arrête à la dernière minute clôturée

Une bougie en formation peut encore changer. Le collecteur s'arrête donc à la dernière bougie entièrement clôturée.

Soit $t$ le temps Unix courant en millisecondes, $\Delta=60{,}000$ millisecondes la durée d'une minute et $t_{\mathrm{closed}}$ l'heure d'ouverture de la dernière bougie clôturée. Alors :

$$
t_{\mathrm{closed}}
=
\left\lfloor\frac{t}{\Delta}\right\rfloor\Delta-\Delta.
$$

Soit $H$ le nombre configuré de créneaux. Comme $H$ ouvertures inclusives ne contiennent que $H-1$ intervalles, la première ouverture demandée vaut :

$$
t_{\mathrm{floor}}
=
t_{\mathrm{closed}}-(H-1)\Delta.
$$

Pour le symbole $s$, soit $w_s$ la dernière heure d'ouverture stockée et $k$ le nombre de bougies de chevauchement. La requête incrémentale commence à :

$$
t_{\mathrm{start},s}
=
\max\left(w_s-k\Delta,\ t_{\mathrm{floor}}\right).
$$

Le premier terme recharge volontairement les observations récentes. Le second borne la requête à la fenêtre prudente du projet. Dans la configuration versionnée, $k=5$ et $H=5{,}000$. Une ancienne version soustrayait $H\Delta$ et demandait donc 5 001 créneaux inclusifs. Le code et les tests utilisent maintenant $(H-1)\Delta$.

## Les lignes ClickHouse servent de watermark

Le projet n'utilise ni fichier local de progression ni table de watermark séparée. Au début de chaque cycle, le service interroge `max(open_time)` par symbole dans ClickHouse. Les bougies effectivement stockées constituent l'unique source de vérité.

Ce choix élimine un cas de panne délicat. Si un processus écrit les bougies puis s'arrête avant de mettre à jour un curseur distinct, les deux états divergent. Ici, après un redémarrage, le service relit simplement les lignes réellement enregistrées et reconstruit la prochaine fenêtre de requête.

Chaque cycle comporte trois passes :

1. Les nouveaux symboles reçoivent un backfill initial, limité à la fenêtre REST récupérable.
2. Les symboles existants sont repris depuis leur watermark moins cinq minutes jusqu'à la dernière minute clôturée.
3. Les créneaux manquants candidats situés dans la fenêtre REST sont détectés puis rechargés.

L'échec d'un symbole n'annule pas les insertions des autres. Les écritures sont aussi découpées selon une limite configurable de lignes. Un démarrage à froid sur l'ensemble de l'univers ne devient donc pas un lot unique et sans borne en mémoire.

Le calcul central de la fenêtre tient en quelques lignes :

```python
horizon_floor_ms = earliest_open_ms_for_candle_count(
    last_open_ms=last_closed_ms,
    candle_count=rest_horizon_candles,
    interval_ms=interval_ms,
)

for symbol in symbols:
    watermark_ms = watermarks_ms.get(symbol)
    if watermark_ms is None:
        continue

    overlapped_start_ms = watermark_ms - overlap_candles * interval_ms
    start_ms = max(overlapped_start_ms, horizon_floor_ms)
    if start_ms <= last_closed_ms:
        work_items.append(
            WorkItem(symbol=symbol, start_ms=start_ms, end_ms=last_closed_ms)
        )
```

Aucun curseur n'est cru sur parole au seul motif que le processus précédent a déclaré un succès. La progression est déduite des données durables.

Le contrat de stockage reste volontairement simple :

| Groupe de champs | Type ClickHouse | Invariant avant insertion |
|---|---|---|
| `symbol` | `LowCardinality(String)` | Doit correspondre au marché demandé |
| `open_time`, `close_time` | `DateTime64(3, 'UTC')` | L'ouverture est alignée sur 60 000 millisecondes ; la clôture vaut l'ouverture plus 59 999 millisecondes |
| open, high, low, close (OHLC) | `Float64` | High n'est inférieur à aucune valeur OHLC ; low n'est supérieur à aucune valeur OHLC |
| `volume` | `Float64` | Valeur positive ou nulle |
| `trades` | `UInt32` | Entier positif ou nul |

Les millisecondes Unix ne dépendent pas d'un fuseau horaire. Le writer ne les convertit en objets Python `datetime` horodatés en temps universel coordonné (UTC) qu'à la frontière ClickHouse. Si un driver renvoie un `datetime` sans fuseau, le code l'interprète explicitement en UTC, ce qui évite un décalage silencieux lié au fuseau de la machine.

## Pourquoi la pagination remonte dans le temps

La difficulté la moins intuitive vient de l'API source. La page officielle de l'endpoint `info` donne une règle générale de pagination vers l'avant pour les réponses bornées dans le temps. Les sondages `candleSnapshot` du dépôt ont montré un autre comportement aux limites pour les fenêtres trop larges : la réponse conservait les bougies proches de `endTime` et supprimait l'excédent le plus ancien. Un nouveau sondage réalisé pendant cet audit a renvoyé 5 198 bougies BTC récentes à une minute pour 6 001 créneaux demandés. C'est une observation du 13 juillet 2026, pas une garantie de l'API.

Une pagination vers l'avant ne peut pas récupérer cet excédent. Répéter une requête large avec la même borne de fin renvoie toujours la tranche récupérable la plus récente. Le fetcher recule donc `endTime` d'un intervalle avant la bougie la plus ancienne de la page reçue :

```python
oldest_open_ms = min(candle.open_time_ms for candle in page)
if oldest_open_ms <= start_ms:
    break

next_cursor_end_ms = oldest_open_ms - interval_ms
if next_cursor_end_ms >= cursor_end_ms:
    break

cursor_end_ms = next_cursor_end_ms
```

La vérification de progression du curseur est utile. Si l'API cesse un jour de respecter `endTime`, la boucle s'arrête au lieu de tourner indéfiniment. Le backfill initial, le rattrapage incrémental et la réparation des trous utilisent tous cette même primitive. Leur comportement de pagination ne peut donc pas diverger. Chaque ligne renvoyée doit aussi respecter le symbole et la fenêtre inclusive demandés. Un intervalle, un timestamp, une plage de prix, un volume ou un nombre de transactions invalide fait échouer le symbole avant le stockage.

## Le poids est réservé avant la requête HTTP

Hyperliquid attribue un poids de base de 20 à la plupart des requêtes `info`, puis ajoute une unité par tranche de 60 éléments renvoyés par `candleSnapshot`. Soit $M$ le nombre de bougies renvoyées. Le poids documenté vaut :

$$
W(M)=20+\left\lfloor\frac{M}{60}\right\rfloor.
$$

La taille de la réponse reste inconnue avant l'envoi. Soit $S$ le nombre de créneaux d'une minute demandés. Avant chaque tentative, le client réserve $W(S)$ dans son token bucket, en utilisant $S$ comme borne supérieure prudente de $M$. Une requête initiale de 5 000 créneaux réserve donc $20+\lfloor5{,}000/60\rfloor=103$ unités avant d'atteindre le serveur. L'ancienne implémentation débitait le poids supplémentaire après la réponse, ce qui autorisait une rafale initiale à devancer le budget local.

Les erreurs de transport HTTP, le statut 429 et les erreurs serveur sont retentés jusqu'à quatre tentatives au total, avec une attente exponentielle et une composante aléatoire. Les erreurs client, par exemple une requête invalide, ne sont pas retentées. Relancer une fenêtre ayant échoué reste sûr : la progression vient des lignes stockées et les insertions de chevauchement partagent la même clé logique.

## Le chevauchement est sûr, mais les doublons existent temporairement

La table brute utilise le moteur ClickHouse `ReplacingMergeTree(inserted_at)` et trie par `(symbol, open_time)`. Recharger cinq bougies à chaque cycle est volontaire. Ce chevauchement couvre les erreurs de frontière, les révisions récentes de la source et les arrêts survenus près de la fin d'une insertion.

Idempotent ne signifie pas physiquement unique à chaque instant. ClickHouse élimine les anciennes versions lors des fusions en arrière-plan. Des clés en double peuvent donc coexister avant la fin d'une fusion. Une requête de recherche qui exige exactement une ligne par minute et par symbole doit réduire les versions avec `argMax(..., inserted_at)`, utiliser `FINAL` ou appliquer une déduplication équivalente.

Le compromis est raisonnable : l'ingestion reste simple et résistante aux redémarrages, tandis que le lecteur choisit entre l'unicité logique immédiate et une vitesse de lecture maximale.

## La fraîcheur représente un budget de récupération

La surveillance de la fraîcheur ressemble parfois à un simple agrément de tableau de bord. Dans ce projet, elle protège des données qui finiront autrement par disparaître définitivement de REST.

![Seuils de fraîcheur configurés et horizon REST](images/01_freshness_timeline.png)

Le graphique utilise le fichier `config.toml` versionné, pas des pannes observées en production. Les seuils warning, serious, urgent et critical se situent à 60, 720, 2 880 et 4 320 minutes. La première ouverture de la fenêtre configurée de 5 000 créneaux se trouve 4 999 minutes avant la dernière, ce qui laisse 679 minutes, soit 11 heures et 19 minutes, après le seuil critique. Cette marge vient de la configuration ; elle ne mesure pas la rétention de la source.

La commande de qualité ne contrôle pas seulement le retard. Elle présente la fraîcheur des symboles actifs, les clés brutes en double, les trous candidats, les nombres de lignes quotidiens, les parts ClickHouse actives et les derniers cycles d'ingestion. La fraîcheur est limitée à l'univers Hyperliquid courant. Un contrat retiré ne déclenche ainsi pas une fausse alerte permanente, tandis que son historique reste intact.

La couverture demande une formulation précise. Soient $t_{\min}$ et $t_{\max}$ la première et la dernière ouverture stockées dans une fenêtre mesurée, et $U$ le nombre de clés `(symbol, open_time)` uniques. Le nombre attendu de créneaux d'une minute et le taux observé valent :

$$
E=\left\lfloor\frac{t_{\max}-t_{\min}}{\Delta}\right\rfloor+1,
\qquad
Q=\frac{U}{E}.
$$

$Q<1$ signale des créneaux absents du stockage. Il ne prouve pas une panne d'ingestion, car la documentation officielle ne garantit pas une bougie pour chaque minute sans transaction. La réparation peut recharger la fenêtre en toute sécurité, mais une minute réellement absente à la source peut rester visible dans les rapports suivants.

## La compression a été mesurée, pas devinée

Les colonnes d'une série de bougies à une minute n'ont pas la même structure. Les timestamps avancent régulièrement, les prix voisins restent souvent proches, le nombre de transactions est un entier borné et le volume fractionnaire est bruité. Un codec générique a peu de chances de convenir aux quatre formes.

Soit $B_u$ le nombre d'octets non compressés, $B_c$ le nombre d'octets compressés et $N$ le nombre de lignes. Le ratio de compression $R$ et le nombre d'octets compressés par ligne $b$ valent :

$$
R=\frac{B_u}{B_c},
\qquad
b=\frac{B_c}{N}.
$$

Le choix du schéma repose sur $b$, où une valeur plus faible est préférable. Un ratio seul peut paraître flatteur simplement parce que le type non compressé est plus large.

![Benchmark mesuré de compression par codec](images/02_compression_benchmark.png)

Ces valeurs proviennent du benchmark séparé du dépôt sur 3 162 240 lignes de contrats perpétuels crypto. Elles ne mesurent pas la table Hyperliquid en production. Sur cet échantillon, la meilleure combinaison sans perte applique Delta et Zstandard (ZSTD) aux prix open, high, low et close, ZSTD seul au volume fractionnaire, puis T64 et ZSTD au nombre de transactions. Elle occupe 16.14 octets par ligne contre 19.55 pour la référence de style production, soit une baisse d'environ 17.4 %. Gorilla avec ZSTD occupe 27.72 octets par ligne, contre 30.54 pour LZ4 par défaut.

Le schéma Hyperliquid créé reprend la structure de codecs mesurée tout en choisissant le niveau ZSTD 12 : `DoubleDelta` pour les timestamps, `Delta` pour les prix, ZSTD seul pour conserver le volume `Float64` sans perte et `T64` pour le nombre de transactions `UInt32`. Le benchmark étaye le classement des codecs. Il ne détermine pas l'empreinte finale de ce jeu de données précis.

## Ce que la conception garantit, et ce qu'elle ne peut pas garantir

Le service résiste aux redémarrages tant que les données restent dans la fenêtre récupérable de la source. Il recalcule son état à partir des lignes stockées, recharge un chevauchement borné, isole les échecs par symbole et tente de réparer les trous internes récents. Les tests unitaires couvrent l'arithmétique temporelle, la pagination, le parsing, la construction des fenêtres de travail, la gestion des trous et les écritures par lots.

La conception ne peut pas récupérer une panne plus ancienne que l'historique REST. Une fenêtre configurée à 5 000 bougies n'est pas un accord de niveau de service, et la disponibilité s'exprime en bougies plutôt qu'en minutes exactes de temps civil. Un historique profond exige toujours une autre source, par exemple une archive, ou une collecte continue avant l'expiration de la fenêtre.

Une autre limite mérite d'être explicite : le dépôt ne contient aucun rapport de qualité de production figé. Le modèle de panne, le comportement testé, les seuils configurés et l'expérience mesurée sur les codecs sont documentés. En revanche, les fichiers versionnés ne permettent pas d'affirmer un uptime de production, un débit d'ingestion, un nombre de lignes accumulées ou le ratio de compression de la table réelle.

La frontière utile est nette : les tests unitaires étayent l'arithmétique temporelle, la validation, la pagination, le calcul du poids des retries, le chemin de déduplication et les écritures découpées. L'expérience de compression du dépôt étaye le choix des codecs. L'uptime, le débit, la couverture réelle et le coût de stockage en production restent inconnus.

## Références

- [Endpoint `info` et `candleSnapshot` d'Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Limites et poids des requêtes Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [`ReplacingMergeTree` dans ClickHouse](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree)
- [Codecs de compression des colonnes ClickHouse](https://clickhouse.com/docs/data-compression/compression-in-clickhouse)
