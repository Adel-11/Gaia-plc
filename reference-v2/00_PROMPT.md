# Mission : Gaia PLC Generator v2

Tu as codé et déployé le générateur Gaia (base d'équipements, CSV FUXA, IO List Excel), hébergé sur le Raspberry Pi de Gaia et déployé par Coolify depuis GitHub. Cette mission le **refond en profondeur**.

Il devient l'outil de conception du programme automate CODESYS. Il doit savoir :
- modéliser les types d'objets et leurs instances ;
- les afficher graphiquement ;
- éditer leur code ST ;
- régler les paramètres et les communications ;
- importer et exporter les fichiers Excel du projet ;
- exporter tout ou partie du programme en XML PLCopen importable dans CODESYS **sans rien casser**.

Le commanditaire est **Adel**, électricien automaticien, propriétaire du projet.

---

## 0. Avant d'écrire une ligne de code

1. **Lis tout le pack**, dans cet ordre :
   1. ce fichier ;
   2. `01_SPEC_TECHNIQUE.md` (conventions du programme, formats Excel, règles d'export PLCopen) ;
   3. les info-bulles des en-têtes de chaque fichier de `02_excel` ;
   4. les XML de `03_plc`.
2. **Ouvre `03_plc/Gaia_V7_reference_full.xml`** et lis réellement :
   - un FB d'équipement complet (`FB_INST_DIP1500`) ;
   - un FB d'UPI ;
   - `GFC_COM_IN` ;
   - `GFC_EXC` et une `FC_EXC_...` ;
   - `PLC_PRG`.

   Tout ce que le générateur produit doit ressembler à ça.
3. **Fais l'inventaire de ta v1** : ce qui se garde, ce qui se jette, ce qui doit migrer. Ta v1 repose sur des conventions **obsolètes** (`GVL_SCADA`, `DUT_SCADA_*`, `GVL_UPI`, `DUT_UPI_*`, `GFC_EXC_IN/OUT`).
4. **Envoie à Adel, avant de coder** :
   - tes questions ;
   - ton modèle de données (entités et relations) ;
   - ton plan de livraison par étapes (section 9) ;
   - la liste des points du pack que tu trouves ambigus.

   Attends sa validation.

**Règles de conduite pendant toute la mission :**
- **Ne prends aucune initiative** sur les conventions automate d'Adel (noms, GVL, canaux Modbus, structure des FB) sans lui demander. La section 2.7 de la spec liste les interdits.
- **Sois honnête sur l'état** : ce qui est testé, ce qui ne l'est pas, ce qui demande une action manuelle dans CODESYS.
- **Pas de fonctionnalité "à moitié"** présentée comme finie. Si quelque chose est reporté, dis-le et note-le dans le journal des modifications.
- **Interface en anglais**, comme les fichiers Excel et le code. Adel peut demander une autre langue.

## 1. Infrastructure (rappel, inchangé)

- **Hébergement** : Raspberry Pi 4 `raspberrypi-gaia`, déploiement par **Coolify** depuis la branche `main` de GitHub. Un `Dockerfile` unique à la racine, un seul conteneur (API + frontend compilé).
- **Ce que tu ne fais pas** : pas de build d'image en local, pas de registre, pas de GitHub Actions, pas de multi-arch, pas de docker-compose, pas de config Nginx, Traefik ou Cloudflare.
- **FUXA** tourne sur le même Pi : ne touche ni à FUXA ni à son port.
- **Données persistantes** dans un volume déclaré, avec la migration des données de la v1. Si la migration est trop coûteuse, **demande à Adel** s'il préfère repartir des Excel de ce pack, qui sont plus à jour que ta base actuelle.
- **Stack** : garde-la, sauf raison forte à lui exposer d'abord.
- **Journal des modifications** (`CHANGELOG.md`) mis à jour à chaque livraison.

## 2. Modèle de données attendu (à proposer puis valider avec Adel)

Au minimum ces entités. Les relations doivent permettre toutes les vues et tous les exports décrits plus bas.

- **ObjectType** : nom, nature (Equipment / UPI), description, communication, dossier projet.
  - **5 DUT** (`COM`, `EXC`, `INT`, `IO`, `PA` ; 3 pour une UPI), chacun avec une liste **ordonnée** de membres : section, nom, type IEC, valeur par défaut, unité, commentaire, type d'échange.
  - **FB** : déclaration générée (`VAR_IN_OUT`, `VAR`), locales en plus (timers...), corps ST.
  - **Carte de registres RTU** (4 tables) et propriétés RTU par défaut.
  - **Points d'échange supervision par défaut** (membres `SCADA_` / `HMI_` à adresser).
- **Instance** : nom, type, description, repère terrain.
- **TcpClient** (FUXA, HMI...) : plages d'adresses, ordre des mots, IP. **TcpPoint** : table, adresse, client, instance, membre, mot (LSW/MSW).
- **RtuLine** : port, vitesse, parité, bits, timeout. **RtuSlave** : instance, ligne, adresse esclave, device CODESYS. Les variables `GVL_MODBUS` sont rattachées par canal.
- **Rack / Module / IoChannel** : slot, module, device CODESYS, voie, variable `GVL_IOMAP`, membre `GVL_IO` de l'instance, signal, échelle.
- **PaValue** : instance, membre, valeur d'instance (vide = défaut du type), "modifiable depuis la supervision".
- **ExchangeFunction** (`FC_EXC_<A>_<B>`) : type A, type B, liste ordonnée de recopies (source `EXC_<A>.<membre>`, destination `EXC_<B>.<membre>`, sens), corps ST.
- **ExchangeCall** : FC, instance A, instance B. Ce sont les appels de `GFC_EXC`.
- **Function** (FC "normales", ex. `FNC_WORDS_TO_REAL`) : interface et code ST.
- **Objets générés** : `GFC_COM_IN/OUT`, `GFC_IO_IN/OUT`, `GFC_EXC`, `GFB_INST`, `GFB_UPI`, `PLC_PRG`. Ils sont **calculés** à partir du modèle et ne sont pas saisis.
- **CodesysSnapshot** : le dernier export CODESYS importé par Adel. Il fournit les ObjectId, l'en-tête XML, les dossiers, et l'état de référence pour savoir ce qui a changé (export partiel).

## 3. Vues et fonctionnalités

Chaque vue est décrite avec ce qu'elle doit permettre. Le style graphique est libre mais doit rester sobre, clair et lisible. Adel te précisera l'ergonomie.

### 3.1 Tableaux des types et des instances

- **Deux tableaux**, Types et Instances :
  - tri et filtre ;
  - recherche ;
  - colonnes utiles (nature, communication, nombre d'instances, état "modifié depuis le dernier export CODESYS").
- **Actions** : créer, dupliquer, renommer (avec mise à jour de toutes les références), supprimer (avec la liste des impacts avant confirmation).
- **Ouverture** : un clic sur un type ou une instance ouvre sa **vue objet** (3.2).

### 3.2 Vue objet (graphique)

Voir `05_mockups/vue_objet.png`. Le FB est au centre, entouré d'étiquettes reliées au FB, une par DUT et par élément lié.

- **Disposition à confirmer avec Adel.** Proposition : à gauche les DUT passés en `VAR_IN_OUT` (`DUT_PA`, `DUT_COM`, `DUT_EXC`, `DUT_IO`), à droite `DUT_INT` et les éléments liés (`FC_EXC` où l'objet intervient, table RTU, points TCP).
- **Vue d'un type** : le FB et ses DUT.
- **Vue d'une instance** : la même vue, avec les données de l'instance (variables GVL, adresses TCP, voies, adresse esclave, valeurs PA). Les étiquettes affichent un résumé, par exemple "COM : 26 membres, 12 points TCP".
- **Clic sur une étiquette** : ouvre un **éditeur de code ST** (Monaco ou CodeMirror, avec coloration ST, numéros de ligne, recherche).
  - Pour un DUT : sa déclaration `TYPE ... STRUCT ... END_STRUCT END_TYPE`, générée à partir des membres.
  - Pour le FB : déclaration et corps.
- **Synchronisation à double sens** :
  - un membre ajouté dans le tableau du DUT apparaît dans le code ;
  - un code ST modifié et validé est relu et met à jour les membres.

  Si le code ne se relit pas, affiche l'erreur et la ligne, et n'écrase rien.
- **Tableau des membres** à côté du code, éditable : type par liste déroulante, valeur par défaut, section, commentaire.
- **Glisser-déposer** pour réordonner les membres. L'ordre compte, c'est l'ordre de déclaration.

### 3.3 Création d'un type d'objet

C'est un assistant en étapes, **simple et agréable à utiliser**, avec aperçu en direct du code généré :

1. **Identité** : nom (contrôle de syntaxe CODESYS et d'unicité), nature (Equipment / UPI), description, communication (Modbus RTU, Wired I/O, aucune...).
2. **DUT** : un onglet par DUT. Ajout de membres en tableau, sections, types en liste déroulante (types IEC, DUT existants, `DUT_Union_Real_Word`, tableaux).
   - Blocs prédéfinis à insérer en un clic, par exemple le **bloc standard des modes local/auto** (membres `SCADA_/HMI_/WEBVISU_INPUT_LOCAL_REQUEST` etc.), copié depuis un type existant.
   - Option "partir d'un type existant" (copie complète).
3. **Communication RTU**, si besoin : carte des registres (4 tables), ordre des mots, propriétés. Les membres `COM_` correspondants sont proposés automatiquement.
4. **Points supervision** : quels membres `SCADA_` / `HMI_` sont échangés en Modbus TCP. Le doublon HMI à +1000 est proposé automatiquement.
5. **FB** : squelette généré avec l'interface standard et les 3 blocs commentés INPUTS / PROCESSING / OUTPUTS, plus le bloc standard de modes de conduite si l'option est choisie. Le corps s'édite dans l'éditeur ST.
6. **Récapitulatif** : liste des objets qui seront créés, puis bouton créer.

### 3.4 Vue graphique des échanges (FC_EXC)

Voir `05_mockups/vue_echange.png` : Objet 1 ↔ FC_EXC_Objet1_Objet2 ↔ Objet 2.

- **Canvas** (React Flow ou équivalent) avec une palette des instances. On glisse des instances dans l'espace de dessin.
- **Relier deux instances** :
  - si une `FC_EXC_<typeA>_<typeB>` existe déjà pour ces deux types, elle est proposée ;
  - sinon on la crée.

  Le lien crée l'appel dans `GFC_EXC`.
- **Ouvrir une FC** affiche une vue de câblage : à gauche les membres du `DUT_EXC` de A, à droite ceux de B. On relie un membre source à un membre destination en tirant un fil.
  - Contrôle de type : on ne relie que des types compatibles.
  - Sens : les sorties (`..._OUTPUT_...`) vont vers les entrées (`..._INPUT_...`). Avertis si ce n'est pas le cas.
- **Code ST de la FC** : il est généré à partir des fils et reste **éditable**. Une modification du code est relue et redessine les fils (même règle de synchronisation qu'en 3.2).
- **Portée d'une modification** : une FC est commune à toutes les paires d'instances de ces deux types. Affiche-le clairement.
- **Mise en page** : la disposition du canvas est enregistrée.

### 3.5 Tableaux du code généré et des fonctions

- **Objets générés** (`GFC_COM_IN/OUT`, `GFC_IO_IN/OUT`, `GFC_EXC`, `GFB_INST`, `GFB_UPI`, `PLC_PRG`) : un tableau à part, **lecture du code** dans l'éditeur ST en lecture seule.
  - Ils sont recalculés à chaque changement du modèle.
  - Affiche la date du dernier calcul et la différence avec le dernier export CODESYS.
- **Fonctions normales** (`FNC_*`) : un autre tableau, avec consultation et édition du code.
- **FC d'échange** : listées dans le même tableau, ou dans un onglet voisin. Elles s'éditent aussi depuis la vue des échanges.

### 3.6 Paramètres PA

- **Tableau par instance** : membre, type, valeur par défaut du type, valeur d'instance (vide = défaut), unité, "modifiable depuis la supervision".
- **Contrôle de saisie** selon le type IEC (REAL, WORD dans 0..65535, BOOL...).
- **Repérage visuel** des valeurs différentes du défaut.
- **Vue par type** possible : un paramètre, toutes les instances côte à côte.
- **Avertissement** à afficher : les valeurs PA dans le PLC peuvent avoir été changées depuis une supervision, et modifier `DUT_PA` réinitialise les persistantes.

### 3.7 Modbus RTU

- **Tableaux** :
  - lignes ;
  - esclaves ;
  - registres par instance, avec les colonnes du fichier `MODBUS_RTU`.
- **Page de réglage d'une ligne** : port du PLC, vitesse, parité, bits de données, bits de stop, timeout, nom des devices CODESYS. Listes déroulantes, avec les mêmes valeurs que les Excel.
- **Page de réglage d'un esclave** : adresse (1..247, unique sur la ligne), device CODESYS, ordre des mots, canaux (cyclique / trigger).
- **Vue graphique** : le PLC, chaque port, la ligne RS-485, et les esclaves accrochés avec leur adresse. Un clic sur un esclave ouvre sa table de registres.
- **Contrôles** :
  - adresse esclave en double sur une ligne ;
  - vitesse incohérente entre ligne et esclave ;
  - registres qui se chevauchent dans un canal.

### 3.8 Modbus TCP

- **Tableaux** : clients, et les 4 tables d'adresses (colonnes du fichier `MODBUS_TCP`), avec filtre par client et par instance.
- **Page de réglage d'un client** : nom, rôle, plage d'adresses par table, ordre des mots, IP.
- **Vue graphique** :
  - le PLC serveur au centre, les clients autour ;
  - pour chaque client, une **carte d'occupation des adresses** par table (adresses utilisées, libres, hors plage).
- **Ajouter les points d'une instance** : en un clic, avec la première adresse libre de la plage du client. Le doublon HMI suit la règle FUXA + 1000.
- **Contrôles** :
  - adresse en double ;
  - adresse hors plage ;
  - dépassement de la taille des zones `GVL_MODBUS` / serveur (spec 5.7).

### 3.9 E/S physiques

- **Vue rack** : les 11 slots, avec le module et le device CODESYS de chacun.
- **Tableau des voies** : affectation d'un membre `DUT_IO` d'une instance à une voie libre.
- **Contrôles** :
  - voie en double ;
  - type de voie incompatible (un BOOL sur une voie AI, par exemple).

### 3.10 Import / export

**Excel.** Les fichiers de `02_excel` sont le format d'échange du générateur :
- **import** de chaque fichier (TYPE, IO_PHYSICAL, MODBUS_TCP, MODBUS_RTU, PARAMETERS_PA, EXCHANGES_EXC) ;
- **export** de chacun, en partant des `templates/` ;
- règles de la spec section 3.3 : aller-retour sans perte, colonnes lues par en-tête, `LISTS` à jour ;
- **rapport d'import** : ajouts, modifications, erreurs, à valider avant d'écrire en base.

**Export PLCopen.** C'est le cœur de la mission. Applique toute la section 5 de la spec.
- **Complet** : tout le programme.
- **Partiel, centré sur un élément** : un type, une instance, une FC, une GVL... Le générateur calcule les dépendances (spec 5.5) et propose la liste des objets à exporter. Adel peut décocher ou ajouter des objets avant de générer.
  - Exemple : « j'ai créé un objet, exporte-le avec tout ce qui y est relié » donne son type (DUT + FB) s'il est nouveau, les GVL, les GFC modifiées, `GFB_INST`, et `PLC_PRG` seulement si nécessaire.
- **Résumé avant export** :
  - objets inclus et pourquoi ;
  - objets à supprimer à la main ;
  - actions manuelles dans les devices ;
  - avertissements PA.

  Le même résumé est téléchargeable en texte.
- **Validation automatique** de chaque XML avant de le proposer au téléchargement : schéma XSD (`06_schema`) et références (spec 5.4). Si une étape échoue, **pas de téléchargement**, mais l'erreur exacte.

**Import d'un export CODESYS.** Adel importe son dernier export PLCopen (objets seuls). Le générateur :
- met à jour les ObjectId, l'en-tête et les dossiers ;
- compare avec son modèle et affiche les différences : objets modifiés dans CODESYS mais pas dans le générateur, et l'inverse.

  C'est ce qui évite d'écraser un changement fait directement dans CODESYS. **Ne remplace jamais silencieusement** du code présent dans CODESYS par la version du générateur.

**CSV FUXA.** Export depuis les points TCP du client FUXA, au format strict de la spec section 4, en gardant les identifiants de tags existants.

## 4. Qualité du code ST généré

- **Même aspect que le programme de référence** : en-têtes de bloc, sections, alignement des `:=`, commentaires en anglais, indentation par tabulation.
- **Code lisible plutôt qu'optimisé** : c'est un choix explicite d'Adel.
- **Aucune ligne générée sans `;`**. Aucune référence vers une variable inexistante : vérification automatique avant chaque export.

## 5. Tests obligatoires (automatisés, dans le dépôt)

1. **Aller-retour Excel** : import des fichiers de `02_excel`, export, puis comparaison cellule à cellule des lignes de données. Résultat identique attendu.
2. **Programme de référence** : à partir des fichiers de `02_excel` et du snapshot `Gaia_V6_export_CODESYS.xml`, l'export complet reproduit `Gaia_V7_reference_full.xml` : mêmes objets, mêmes membres et types, mêmes recopies dans les GFC, mêmes FC_EXC et mêmes appels. L'alignement et les espaces peuvent différer. Les FB et les FNC sont restitués à l'identique.
3. **Schéma XSD** : sur chaque XML exporté.
4. **Export partiel** : régénérer les deux exemples de `03_plc/examples` à partir du modèle donne les mêmes objets.
5. **Contrôles de cohérence** : doublons d'adresses, de voies, d'adresses esclaves ; références ST ; bornes de tableaux ; bits dans leur octet.
6. **CSV FUXA** : regénérer `04_fuxa/SCADA_V1-devices_v4.csv` donne le même fichier, octet pour octet, avec les mêmes identifiants.

## 6. Ce qu'Adel teste lui-même

- **Import réel dans CODESYS** : à chaque étape de livraison, il importe un export partiel puis un export complet dans une **copie** de son projet et compile. Livre-lui le XML et le résumé d'export.
- **Cas à faire valider en priorité** : export de `GVL_PA` modifiée (spec 5.6), initialisation de structure PA, nouveau dossier, nouvelle FC_EXC.

## 7. À ne pas faire

- Générer un XML qui contient l'objet `Application`, un device, une tâche ou une librairie.
- Exporter `DUT_Union_Real_Word`.
- Renommer ou déplacer une variable de `GVL_MODBUS` ou `GVL_IOMAP`, ou y ajouter autre chose que des variables d'échange.
- Changer une convention de la spec section 2 sans accord écrit d'Adel.
- Écraser du code venant de CODESYS sans le montrer à Adel.
- Livrer une fonctionnalité non testée en la présentant comme finie.

## 8. Contenu du pack

| Dossier | Contenu |
|---|---|
| `00_PROMPT.md` | ce document |
| `01_SPEC_TECHNIQUE.md` | conventions du programme, formats Excel, CSV FUXA, règles PLCopen, export partiel, points ouverts |
| `02_excel/` | fichiers TYPE (6 types), fichiers projet (IO_PHYSICAL, MODBUS_TCP, MODBUS_RTU, PARAMETERS_PA, EXCHANGES_EXC), `templates/` vides |
| `03_plc/Gaia_V6_export_CODESYS.xml` | dernier export brut de CODESYS (référence de format, ObjectId, `GVL_PA` persistante) |
| `03_plc/Gaia_V7_reference_full.xml` | programme actuel complet (cible de l'export complet) |
| `03_plc/examples/` | exports partiels d'exemple + listings ST |
| `04_fuxa/` | CSV FUXA actuel |
| `05_mockups/` | `vue_objet.png`, `vue_echange.png` (croquis d'Adel) |
| `06_schema/tc6_xml_v201.xsd` | schéma PLCopen |
| `07_reference_scripts/` | scripts Python qui ont produit les Excel et les XML : **référence d'implémentation**, chemins en dur, pas à exécuter tels quels |

Rôle des scripts de référence :
- `build_exc.py` : FC_EXC, GFC_EXC et export partiel ;
- `merge_v7.py` : fusion dans le programme complet ;
- `build_pa.py` : modification d'objets et export partiel ;
- `xlgen*.py` : Excel avec en-têtes, info-bulles, listes ;
- `points.py` : adressage TCP FUXA/HMI ;
- `check.py` : contrôle des références ST.

## 9. Livraison par étapes (à valider avec Adel)

Proposition de découpage. Chaque étape est déployée, testée par Adel, et notée dans le CHANGELOG avant de passer à la suivante.

1. **Modèle de données + import/export Excel + migration v1.** Tests 1 et 6.
2. **Génération du programme et export PLCopen complet et partiel**, avec résumé et validation. Tests 2 à 5, puis import réel par Adel.
3. **Import d'un export CODESYS** (ObjectId, différences).
4. **Vue objet + éditeur ST synchronisé.**
5. **Assistant de création de type.**
6. **Vue graphique des échanges.**
7. **Pages PA, Modbus RTU, Modbus TCP, E/S** avec leurs vues graphiques.
8. **Tableaux du code généré et des fonctions**, finitions.

À la fin de chaque étape, envoie à Adel :
- ce qui est fait ;
- ce qui est testé et comment ;
- ce qui ne l'est pas ;
- les actions manuelles éventuelles.
