# Mission : Gaia IO Generator (premier jet + premier déploiement)

Tu vas construire une application web qui gère une base d'équipements industriels et qui génère deux fichiers :
le CSV d'import des tags pour le SCADA FUXA, et l'IO List Excel (table d'échange Modbus de l'automate).
Tu livres le code sur un dépôt GitHub. Le déploiement est fait par Coolify à partir de ce dépôt.

Lis toute la documentation fournie avant d'écrire du code (voir « Documentation fournie » en bas).
Si un point est ambigu ou contredit la doc, pose la question avant de coder plutôt que de deviner.

## 1. Contexte métier

- Société : Gaia Refinery (Ottawa), skid de captage de carbone.
- Automate : AutomationDirect ProductivityCODESYS P2CDS-622, programmé sous CODESYS 3.5.
- Le PLC est **serveur Modbus TCP** pour le SCADA (IP actuelle 192.168.0.10, slave id 10).
- Le PLC est **maître Modbus RTU (RS485)** pour les équipements de terrain. Équipements actuels :
  - `ADM100W` : centrale de mesure Trumeter (input registers float 32 bits, coils d'alarme).
  - `DIP1500` : pompe péristaltique (coils, input registers, holding registers).
  - `DOSENSOR` : sonde d'oxygène dissous (câblée en E/S, exposée au SCADA).
- SCADA : FUXA, hébergé sur le même Raspberry Pi que ton application.
- Conventions CODESYS du projet, par équipement `<EQ>_<n>` (ex. `DIP1500_1`) :
  `GVL_IO.IO_<EQ>_<n> : DUT_IO_<EQ>`, `GVL_EXC.EXC_<EQ>_<n> : DUT_EXC_<EQ>`,
  `GVL_SCADA.SCADA_<EQ>_<n> : DUT_SCADA_<EQ>`, `GVL_IOMAP.IOMAP_<signal>_<EQ>_<n>`, instance de FB `FB_INST_<EQ>`.
  Les variables échangées avec le SCADA sont préfixées `INPUT_` (SCADA vers PLC) et `OUTPUT_` (PLC vers SCADA).

Aujourd'hui tout est maintenu à la main dans un Excel, puis le CSV FUXA est fabriqué à la main. Le but est d'avoir
une base unique d'où l'on régénère les deux fichiers à chaque modification.

## 2. Infrastructure et déploiement (à lire attentivement)

- Serveur cible : Raspberry Pi 4 (4 Go, Linux 64 bits) nommé `raspberrypi-gaia`, déjà géré par Coolify comme serveur de déploiement.
  FUXA tourne déjà sur ce Pi : ne touche pas à FUXA et n'utilise pas son port.
- **Coolify fait lui-même la conteneurisation et le déploiement** : il clone le dépôt GitHub, construit l'image
  directement sur le serveur, lance le conteneur et redéploie automatiquement à chaque push sur la branche `main`.
- Donc, tu ne dois PAS :
  - construire ou tester d'image Docker en local, ni pousser d'image sur un registre ;
  - mettre en place GitHub Actions, buildx, du multi-arch ou une config spécifique ARM ;
  - écrire de docker-compose, de scripts de déploiement SSH ou de config Nginx/Traefik/Cloudflare.
- Tu dois :
  - fournir un **`Dockerfile` unique et simple à la racine** (Coolify l'utilise avec son build pack « Dockerfile ») :
    un seul conteneur qui sert l'API et le frontend compilé ;
  - écouter sur `0.0.0.0` et sur le port donné par la variable `PORT` (défaut `8000`) ;
  - exposer `GET /api/health` (200 si l'app et la base répondent), pour le healthcheck Coolify ;
  - écrire **toutes les données persistantes dans `/data`** (base SQLite, imports, exports) : Coolify y monte un volume persistant.
    Rien d'important ne doit être écrit ailleurs dans le conteneur ;
  - lire la configuration par variables d'environnement, et fournir un `.env.example` documenté.
- Le build se fait sur le Pi : garde-le léger (pas de dépendance native exotique, pas d'étape qui demande plusieurs Go de RAM).
  Tu n'as rien d'autre à faire pour l'architecture ARM.
- Exposition : un tunnel Cloudflare amène un sous-domaine (ex. `iogen.gaiarefinery.ca`) vers le proxy de Coolify. Le HTTPS est géré
  en amont : l'app sert du HTTP simple et doit fonctionner derrière un reverse proxy (respecter `X-Forwarded-Proto` / `X-Forwarded-For`).
- Il n'y a **pas** de Cloudflare Access devant l'app : elle sera publique sur Internet, donc **l'authentification est obligatoire dans l'app**
  (voir 5.7).

## 3. Stack imposée

- Backend : Python 3.12, FastAPI, SQLAlchemy 2 + Alembic (migrations), SQLite dans `/data/app.db`.
- Excel : openpyxl (lecture du .xlsm de référence et écriture du .xlsx).
- Frontend : React + TypeScript + Vite, compilé dans une étape du Dockerfile (multi-stage) puis servi en statique par FastAPI.
  Un composant de tableau éditable (édition en ligne, tri, filtre, copier-coller depuis Excel si possible).
- Tests : pytest (les tests de génération décrits en 7 sont obligatoires).
- Code, noms de variables et commentaires en anglais. Interface en anglais (l'équipe travaille en anglais).

## 4. Modèle de données (à affiner si besoin, en expliquant tes choix)

Sépare clairement le **catalogue** (les types d'équipement et leur protocole) des **instances** (les équipements réellement installés).

- **Project settings** : nom du projet, référence documentaire de l'IO List (`GAIA-ELEC-1451`) et version, paramètres du device FUXA
  (id `d_...`, nom, IP, port, slave id, polling), gabarit des blocs fixes du CSV FUXA (voir spec).
- **EquipmentType** (catalogue) : code (`ADM100W`), fabricant, modèle, description, protocole (Modbus RTU, Modbus TCP, câblé E/S),
  paramètres de communication par défaut (baud, parité, bits de stop, slave id), ordre des mots 32 bits (ABCD, CDAB...),
  format d'affichage des adresses (décimal ou hexa), lien ou nom de la datasheet, notes.
- **RegisterTemplate** (registres du type d'équipement, côté équipement) : table (coil / discrete input / input register / holding register),
  adresse, libellé, type de donnée, nombre de mots, accès (R, W, RW), sens (`Equipment To Plc`, `Plc To Equipment`, `Two Ways`),
  échelle, offset, unité, valeurs énumérées éventuelles, description, notes de programmation.
  Prévoir qu'un type puisse dépendre d'un mode (cas réel : sur le DIP1500, `0x4005..0x4009` sont UDINT en mode temps et REAL en mode volume).
- **SCADA point template** (points exposés au SCADA par le type d'équipement) : suffixe de variable PLC (`OUTPUT_FREQUENCY`), nom SCADA
  (`{instance} Frequency`), table SCADA, type CODESYS, sens (`Plc To SCADA`, `SCADA To Plc`, `Two Ways`), cible (`FUXA` / `HMI`),
  échelle, unité, diviseur FUXA, DAQ (on/off, intervalle), lien optionnel vers le RegisterTemplate source.
- **Equipment** (instance) : tag (`DIP1500_1`), type, description, repère P&ID / localisation, paramètres de com réels
  (port série ou IP, slave id, baud, parité, bits de stop, polling), noms CODESYS dérivés (GVL, DUT, instance de FB, modifiables),
  champs libres d'aide à la programmation (notes, statut : prévu / câblé / testé), actif ou non.
- **ScadaPoint** (instance, généré depuis les templates puis modifiable ligne par ligne) : chemin complet de la variable PLC,
  nom SCADA, table, adresse affectée, verrouillage d'adresse (oui/non), type CODESYS, type FUXA, diviseur, échelle, unité,
  sens, cible, adresse HMI, DAQ, **id de tag FUXA stable** (`t_xxxxxxxx-xxxxxxxx`), commentaire.
- **Generation log** : date, utilisateur, type de fichier, version, nombre de points, empreinte du fichier.

Affectation des adresses SCADA : automatique et séquentielle par table, dans l'ordre des équipements, avec adresses verrouillables.
Une variable 32 bits prend 2 registres consécutifs. Détecter et bloquer tout chevauchement.

## 5. Fonctions du premier jet

1. **Catalogue** : créer, modifier, dupliquer un type d'équipement et ses registres. Pré-charger ADM100W, DIP1500 et DOSENSOR
   à partir de l'IO List de référence et des datasheets fournies.
2. **Équipements** : ajouter une instance d'un type (génère ses points SCADA et lui affecte des adresses libres), dupliquer une instance
   (`DIP1500_1` vers `DIP1500_2`, renommage automatique), modifier n'importe quelle propriété depuis l'interface, désactiver, supprimer.
3. **Vue table d'échange** : les 4 tables SCADA en tableaux éditables, avec filtres par équipement / sens / table, et les adresses libres visibles.
4. **Import Excel** : charger une IO List au format de référence (.xlsm ou .xlsx) pour créer ou mettre à jour les équipements et les points.
   Toujours afficher un **aperçu des différences** (ajouts, modifications, suppressions) avant d'appliquer.
5. **Import CSV FUXA** : relire un export FUXA pour récupérer les paramètres du device et les ids de tags existants (rapprochement par
   table + adresse, puis par nom). Le CSV est aussi le gabarit des blocs fixes.
6. **Génération** : boutons « Generate FUXA CSV » et « Generate IO List (.xlsx) », avec téléchargement. Avant de générer, une validation :
   erreurs bloquantes (adresses en double, noms en double, caractères interdits, type FUXA manquant) et avertissements (unité vide, etc.).
7. **Authentification** : au minimum un compte administrateur créé au premier démarrage à partir de `ADMIN_USERNAME` / `ADMIN_PASSWORD`,
   mot de passe haché (argon2 ou bcrypt), session par cookie sécurisé (`SECRET_KEY`), toutes les routes `/api` protégées sauf `/api/health`
   et le login. Prévoir la structure pour plusieurs utilisateurs plus tard.
8. **Sauvegarde** : export / import complet de la base en JSON depuis l'interface.

Hors périmètre du premier jet (garder l'architecture prête, ne pas coder) : génération du code CODESYS (DUT, GVL, mapping Modbus),
adresses pupitre Weintek, gestion multi-projets, rapport PDF.

## 6. Formats de sortie

Les formats sont décrits dans `05_specs/FORMAT_CSV_FUXA.md` et `05_specs/FORMAT_IO_LIST_EXCEL.md`.
Ils sont stricts : le CSV est importé tel quel dans FUXA, une virgule ou un espace en trop fait échouer l'import.
En cas de doute, les fichiers réels de `01_io_list_reference` et `02_fuxa_reference` font foi.

## 7. Critères d'acceptation (tests automatiques obligatoires)

1. Base vide, import de `01_io_list_reference/GAIA-ELEC-1451-V1 - IO List.xlsm` puis import de
   `02_fuxa_reference/SCADA_V1-devices_1_export_original.csv`, puis génération du CSV :
   le résultat est identique à `02_fuxa_reference/SCADA_V1-devices_complet_attendu.csv`, **à l'exception des ids des tags nouveaux**
   (générés aléatoirement) et des 2 tags de test existants (`TEST Holding`, `Test descrete`) dont le traitement est à proposer.
   Les ids des tags déjà présents dans l'export original doivent être conservés.
2. Le CSV généré : UTF-8 sans BOM, LF uniquement, 20 virgules par ligne `T@`, se termine par `\n\n\n`.
3. Aller-retour Excel : générer l'IO List, la réimporter dans une base vide, regénérer : mêmes données.
4. L'IO List générée se relit avec openpyxl sans erreur, et ses tableaux structurés sont cohérents (noms de colonnes = en-têtes, `ref` correct).
5. L'application démarre avec un `/data` vide (création de la base, migrations, compte admin) et répond 200 sur `/api/health`.

## 8. Livrables et façon de travailler

- Dépôt GitHub (nom proposé : `gaia-io-generator`), branche `main` déployable à tout moment.
- `README.md` avec : lancement en local pour le développement (sans Docker), variables d'environnement, et la procédure Coolify :
  ressource depuis le dépôt GitHub, build pack Dockerfile, serveur `raspberrypi-gaia`, port exposé, volume persistant monté sur `/data`,
  variables d'environnement, domaine, déploiement automatique au push.
- `CHANGELOG.md` mis à jour à chaque livraison.
- Commits petits et clairs.
- À la fin, un compte rendu honnête : ce qui marche, ce qui est testé, ce qui ne l'est pas, ce qui reste ouvert. Ne dis pas qu'une chose
  marche si tu ne l'as pas exécutée.

## Documentation fournie (dans le zip)

- `01_io_list_reference/GAIA-ELEC-1451-V1 - IO List.xlsm` : IO List actuelle, format de référence (import et export).
- `02_fuxa_reference/SCADA_V1-devices_1_export_original.csv` : export réel de FUXA (gabarit du CSV).
- `02_fuxa_reference/SCADA_V1-devices_complet_attendu.csv` : CSV attendu après génération (fichier de test).
- `03_datasheets/` : protocoles Modbus de l'ADM100W et du DIP1500 (adresses, types, ordre des mots, échelles).
- `04_plc_codesys/GVL_SCADA_et_DUT_extrait.st` : extrait des déclarations CODESYS (GVL_SCADA, DUT_SCADA_*, DUT_EXC_*, DUT_IO_*).
  Attention : ces DUT viennent d'une version plus ancienne du programme. Les noms de variables à jour sont ceux de l'IO List.
  Les commentaires de `DUT_EXC_ADM100W` donnent les unités réelles de la centrale (W, VA, kWh, % de 60 A).
- `05_specs/FORMAT_CSV_FUXA.md`, `05_specs/FORMAT_IO_LIST_EXCEL.md` : règles de format.
- `05_specs/reference_gen_csv.py` : script Python qui a produit le CSV attendu à partir de l'IO List (référence de logique, pas à réutiliser tel quel).
