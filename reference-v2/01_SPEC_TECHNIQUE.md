# Spécification technique : programme CODESYS, fichiers Excel, export PLCopen

Ce document est la référence technique du générateur. `00_PROMPT.md` décrit l'application à construire, celui-ci décrit les données et les formats.

Si les deux documents se contredisent, ou si un point manque, pose la question à Adel. Ne devine pas.

---

## 1. Contexte

- **Automate** : AutomationDirect ProductivityCODESYS **P2CDS-622**, sous **CODESYS V3.5 SP22 Patch 2**.
- **Rack** : base P2-11B, avec 4 cartes : P2-16AD-1 (slot 1), P2-16DA-1 (slot 2), P2-32TD2P (slot 3), P2-32ND3-1 (slot 4).
- **Modbus TCP** : le PLC est serveur.
  - Client **FUXA** (SCADA, sur le Raspberry Pi) : plage 1..1000.
  - Client **HMI locale Rockwell PanelView 800** : même canal, adresses FUXA + 1000.
  - Les deux sont en CDAB pour les REAL.
- **Modbus RTU** : le PLC est maître, une ligne RS-485 à 9600 bauds.
  - **ADM100W** (centrale de mesure) : ordre des mots ABCD.
  - **Kamoer DIP1500** (pompe péristaltique) : adresse 11, ordre des mots CDAB.
- **WebVisu CODESYS** : hébergée dans l'automate, elle lit et écrit directement les variables.

## 2. Organisation du programme

### 2.1 Objets, types et instances

- **Type d'objet** : un modèle réutilisable, par exemple `DIP1500`, `ADM100W`, `DOSENSOR`, `UPI_LOCAL_FUXA`. Il comprend :
  - un FB ;
  - 5 DUT (3 seulement pour une UPI) ;
  - une carte de registres RTU s'il y en a une ;
  - des valeurs PA par défaut.
- **Deux natures de type** :
  - **Équipement** : FB `FB_INST_<type>`, appelé dans `GFB_INST`.
  - **Unité fonctionnelle (UPI)** : FB `FB_<type>` (ex. `FB_UPI_LOCAL_FUXA`), appelée dans `GFB_UPI`.
- **Instance** : un objet réel. Nom `<TYPE>_<n>` pour un équipement (ex. `DIP1500_1`), nom du type pour une UPI unique (ex. `UPI_LOCAL_FUXA`).
- **Noms dérivés de l'instance** :
  - `GVL_COM.COM_<inst>`, `GVL_EXC.EXC_<inst>`, `GVL_IO.IO_<inst>`, `GVL_PA.PA_<inst>` ;
  - instance de FB : `FB_<inst>` pour un équipement, dans `GFB_INST` (ex. `FB_DIP1500_1`) ; `FB_<inst>_1` pour une UPI, dans `GFB_UPI` (ex. `FB_UPI_LOCAL_FUXA_1`).

### 2.2 DUT d'un type

| DUT | Rôle | Variable globale | Passage au FB |
|---|---|---|---|
| `DUT_COM_<type>` | communication. Préfixes : `COM_` Modbus RTU, `SCADA_` FUXA, `HMI_` PanelView, `WEBVISU_` WebVisu | `GVL_COM.COM_<inst>` | `VAR_IN_OUT COM` |
| `DUT_EXC_<type>` | échanges avec d'autres instances (`EXC_`) et avec les UPI (`UPI_`) | `GVL_EXC.EXC_<inst>` | `VAR_IN_OUT EXC` |
| `DUT_INT_<type>` | variables internes | locale `INTERN` du FB | `VAR` |
| `DUT_IO_<type>` | E/S câblées | `GVL_IO.IO_<inst>` | `VAR_IN_OUT IO` |
| `DUT_PA_<type>` | paramètres, avec valeurs par défaut | `GVL_PA.PA_<inst>` (persistante) | `VAR_IN_OUT PA` |

Règles de nommage et de déclaration :
- **Sens dans le nom du membre** : `INPUT_` = vers le PLC ou vers l'objet, `OUTPUT_` = depuis le PLC ou depuis l'objet. Exemples : `SCADA_INPUT_MOTOR_START_STOP`, `HMI_OUTPUT_LOCAL_ACTIVE`, `UPI_INPUT_GLOBAL_LOCAL_FUXA`, `UPI_OUTPUT_LOCAL_ACTIVE`.
- **Valeurs 32 bits échangées en Modbus** : type `DUT_Union_Real_Word`, union déjà présente dans le projet (`Real_value : REAL` / `Word_value : ARRAY[0..1] OF WORD`). Il ne faut **jamais** l'exporter.
- **Sections** : codées dans le commentaire du premier membre d'un bloc, sous la forme `[Section] commentaire`.
- **DUT sans membre** : il garde une ligne `SPARE : BOOL` pour rester compilable.
- **Mot-clé** : `INT` est réservé, d'où le nom `INTERN`.
- **UPI** : seulement `DUT_COM`, `DUT_EXC`, `DUT_INT` ; le FB n'a que `COM` et `EXC` en `VAR_IN_OUT`.

### 2.3 FB : structure imposée

```
FUNCTION_BLOCK FB_INST_<type>
VAR_IN_OUT
    PA  : DUT_PA_<type>;
    COM : DUT_COM_<type>;
    EXC : DUT_EXC_<type>;
    IO  : DUT_IO_<type>;
END_VAR
VAR
    INTERN : DUT_INT_<type>;
    (timers, R_TRIG... si besoin)
END_VAR
```

Le corps a toujours 3 blocs commentés, sur le modèle des FB du programme de référence :
1. **INPUTS** : demandes de mode, mode de conduite, sélection de la source de commande, retours équipement → `INTERN` ;
2. **PROCESSING** : traitement sur `INTERN` uniquement ;
3. **OUTPUTS** : `INTERN` → `COM`, `IO`, `EXC`.

Autres règles :
- Les conversions (REAL ↔ mots, échelles) se font **dans les FB**. Les GFC et les FC_EXC ne font que des recopies.
- Commentaires en anglais. Un code clair et lisible passe avant l'optimisation.
- **Modes de conduite** (dans chaque FB d'équipement) :
  - priorité WEBVISU > HMI > FUXA > auto (UPI) ;
  - local individuel (demande local / auto par source) OU local global (par `FB_UPI_LOCAL_<source>`, reçu via `DUT_EXC`).
- **Paramètres PA de la DIP1500** : une valeur saisie dans la supervision qui a la main est recopiée dans `PA`, champ par champ et seulement si elle change.

### 2.4 Fonctions, GFC, GFB, programme

| Objet | Rôle | Dossier projet |
|---|---|---|
| `FNC_WORDS_TO_REAL`, `FNC_REAL_TO_WORDS` | fonctions utilitaires | `Functions/FC` |
| `FC_EXC_<typeA>_<typeB>` | échanges entre un objet de type A et un de type B. `VAR_IN_OUT EXC_<typeA> : DUT_EXC_<typeA>; EXC_<typeB> : DUT_EXC_<typeB>;` recopies simples dans les deux sens si besoin | `Functions/FC_EXC` |
| `GFC_COM_IN` / `GFC_COM_OUT` | recopies `GVL_MODBUS` ↔ `GVL_COM` (TCP et RTU) | `Functions/GFC` |
| `GFC_IO_IN` / `GFC_IO_OUT` | recopies `GVL_IOMAP` ↔ `GVL_IO` | `Functions/GFC` |
| `GFC_EXC` | un appel de `FC_EXC_...` par paire d'instances, avec les variables `GVL_EXC.EXC_<inst>` | `Functions/GFC` |
| `GFB_UPI` | un appel `FB_<inst>_1(COM := GVL_COM.COM_<inst>, EXC := GVL_EXC.EXC_<inst>);` par UPI | `Function Blocs/GFB` |
| `GFB_INST` | un appel `FB_<inst>(PA := ..., COM := ..., EXC := ..., IO := ...);` par équipement | `Function Blocs/GFB` |
| `PLC_PRG` | ordre du cycle | `Program` |

**Ordre du cycle dans `PLC_PRG`** (à garder) :
```
GFC_COM_IN(); GFC_IO_IN(); GFB_UPI_1(); GFC_EXC(); GFB_INST_1(); GFC_COM_OUT(); GFC_IO_OUT();
```

Exemple de FC d'échange :
```
FUNCTION FC_EXC_UPI_LOCAL_FUXA_DIP1500 : BOOL
VAR_IN_OUT
    EXC_UPI_LOCAL_FUXA : DUT_EXC_UPI_LOCAL_FUXA;
    EXC_DIP1500        : DUT_EXC_DIP1500;
END_VAR
EXC_DIP1500.UPI_INPUT_GLOBAL_LOCAL_FUXA := EXC_UPI_LOCAL_FUXA.UPI_OUTPUT_LOCAL_ACTIVE;
FC_EXC_UPI_LOCAL_FUXA_DIP1500 := TRUE;
```

Son appel dans `GFC_EXC` :
```
FC_EXC_UPI_LOCAL_FUXA_DIP1500(
    EXC_UPI_LOCAL_FUXA := GVL_EXC.EXC_UPI_LOCAL_FUXA,
    EXC_DIP1500        := GVL_EXC.EXC_DIP1500_1);
```

Le nom d'une FC d'échange suit le **type**, pas l'instance. Une deuxième pompe réutilise la même FC avec un appel de plus.

### 2.5 GVL

| GVL | Contenu | Attributs |
|---|---|---|
| `GVL_COM`, `GVL_EXC`, `GVL_IO` | une variable par instance | `{attribute 'qualified_only'}` |
| `GVL_PA` | objet **Persistent Variables** (`VAR_GLOBAL PERSISTENT RETAIN`), une variable par équipement | `qualified_only` (voir 5.6) |
| `GVL_MODBUS` | variables d'échange Modbus **mappées dans l'arbre des devices** | aucun |
| `GVL_IOMAP` | variables mappées sur les voies des cartes | `qualified_only` |

### 2.6 Recopies générées

- **TCP, client vers PLC** (coils, holding registers) : `GVL_COM.COM_<inst>.<membre> := GVL_MODBUS.<tableau>[...];`. Dans l'autre sens pour les entrées TOR et les input registers.
- **Coils et entrées TOR** :
  - coil d'adresse `a` : `GVL_MODBUS.Coils_TCP_HMI[(a-1) DIV 8].((a-1) MOD 8)` ;
  - entrée TOR `a` : `GVL_MODBUS.Discret_input_TCP_HMI[(a-10001) DIV 8].((a-10001) MOD 8)`.
- **Registres** : index = adresse - 1. Un REAL donne 2 lignes, `.Word_value[0]` (adresse basse, mot bas) puis `.Word_value[1]`.
- **RTU** : le tableau du canal est recopié en entier, par exemple `GVL_COM.COM_ADM100W_1.COM_INPUT_IR := GVL_MODBUS.ADM100W_InputRegisters;`.
- **E/S** : `GVL_IO.IO_<inst>.<membre> := GVL_IOMAP.<var>;` pour les entrées, et l'inverse pour les sorties.
- **Mise en forme** : prends modèle sur `Gaia_V7_reference_full.xml`. En-têtes de section, blocs `//--- <instance>`, alignement des `:=`, commentaire `// adresse | nom`.

### 2.7 Interdits sans accord explicite d'Adel

- **Renommer** une variable de `GVL_MODBUS` ou de `GVL_IOMAP`. Elles sont mappées dans l'arbre des devices.
- **Mettre dans `GVL_MODBUS`** autre chose que des variables d'échange.
- **Changer les canaux Modbus** : lectures cycliques ; écritures DIP1500 sur front montant de `COM_OUTPUT_CMD_TRIGGER` et `COM_OUTPUT_CONFIG_TRIGGER`.
- **Renommer par instance les variables RTU de `GVL_MODBUS`.** Elles sont nommées par type aujourd'hui (`DIP1500_Coils`...). Avec une deuxième instance du même type, il y aura conflit. Le renommage (`DIP1500_1_Coils`) n'est pas encore décidé. Ton modèle doit le permettre, mais tu ne l'appliques pas.
- **Changer une convention** de cette section.

## 3. Fichiers Excel (dossier `02_excel`)

Chaque fichier contient :
- un onglet `README` ;
- un onglet caché `LISTS`, qui porte les listes déroulantes ;
- des en-têtes verts avec une **info-bulle** par colonne. **Les info-bulles font partie de la spec** : lis-les toutes.

Les cellules jaunes "TO CHECK" sont des valeurs non confirmées. Traite-les comme du texte.

### 3.1 Fichiers TYPE : `TYPE_<type>.xlsx`, un par type

| Onglet | Contenu |
|---|---|
| `TYPE` | clé/valeur : type, nature, description, FB, dossier projet, communication, noms des DUT |
| `DUT_COM` `DUT_EXC` `DUT_INT` `DUT_IO` `DUT_PA` | une ligne par membre, dans l'ordre. Colonnes : Section, Member, Type, Default value, Unit, Comment, Exchange |
| `RTU_DEFAULT` | clé/valeur : propriétés RTU de l'équipement |
| `RTU_COILS` `RTU_DI` `RTU_IR` `RTU_HR` | carte des registres, une ligne par adresse ; un 32 bits occupe 2 lignes (Word = MSW/LSW) |
| `TEMPLATE_PA` `TEMPLATE_RTU` `TEMPLATE_EXC` `TEMPLATE_TCP` `TEMPLATE_IO` | lignes à copier dans les fichiers projet pour créer une instance, avec les marqueurs `<INSTANCE>`, `<INSTANCE_B>`, `<SOURCE>`, `<DESTINATION>` |

- `TEMPLATE_EXC` contient des lignes d'appel pour l'onglet `CALLS` d'`EXCHANGES_EXC`.
- `TEMPLATE_TCP` indique dans sa colonne Comment l'onglet de destination.

### 3.2 Fichiers projet (instances)

| Fichier | Onglets |
|---|---|
| `IO_PHYSICAL` | `RACK` (slot, module, device CODESYS `Module_n`, statut) et `IO` (une ligne par voie) |
| `MODBUS_TCP` | `CLIENTS` (plages, ordre des mots, IP), puis `COILS`, `DISCRETE_INPUTS`, `INPUT_REGISTERS`, `HOLDING_REGISTERS`, une ligne par adresse |
| `MODBUS_RTU` | `LINES` (port série), `SLAVES` (une ligne par instance RTU), un onglet par instance RTU |
| `PARAMETERS_PA` | un onglet par équipement : défaut du type, valeur d'instance (vide = défaut), modifiable depuis la supervision Oui/Non |
| `EXCHANGES_EXC` | `FC_EXC` (une ligne par FC d'échange), `CALLS` (une ligne par appel dans `GFC_EXC`), un onglet `<typeA>-<typeB>` par FC (sens, source, destination, type) |

Les `templates/` contiennent les mêmes fichiers vides.

### 3.3 Règles d'import et d'export

- **Aller-retour sans perte** : import puis export = mêmes onglets, mêmes en-têtes dans le même ordre, mêmes valeurs.
- **Lis les colonnes par leur en-tête**, pas par leur position.
- **Exporte en partant des templates** pour garder la mise en forme, les info-bulles, `LISTS` et les validations (lignes 2 à 2000).
- **Mets à jour `LISTS` à l'export** : instances, types, FC d'échange, etc.
- **Contrôles à l'import** :
  - adresse en double dans une table ;
  - voie d'E/S affectée deux fois ;
  - type inconnu ;
  - instance RTU absente de `SLAVES` ;
  - adresse TCP hors de la plage de son client ;
  - membre source ou destination absent du DUT_EXC du type ;
  - FC appelée dans `CALLS` mais pas déclarée dans `FC_EXC`.

  Rapporte ces erreurs à l'utilisateur sans écraser ses données.

## 4. CSV FUXA (format strict)

Le modèle est `04_fuxa/SCADA_V1-devices_v4.csv`. Une virgule ou un espace en trop et l'import échoue.

- **Encodage** : UTF-8 sans BOM, LF seulement, le fichier se termine par `\n\n\n`.
- **Ligne de tag** : `T@,d_abeaaea7-b8914e5b,t_xxxxxxxx-xxxxxxxx,<nom>,,<Bool|Float32MLE|UInt16>,<memaddress>,<adresse>,1,,,,,,,,false,60,false,false,` (20 virgules et une virgule finale).
- **memaddress** : `000000` coils, `100000` entrées TOR, `300000` input registers, `400000` holding registers.
- **Adresse** : index relatif ; pour une entrée TOR, c'est l'adresse Modbus - 10000.
- **Identifiants** : `t_` + 8 hex + `-` + 8 hex. **Garde les identifiants existants** d'un export à l'autre (sinon les vues FUXA perdent leurs liens).
- **Contenu** : le CSV est généré depuis les lignes `Client = FUXA` de `MODBUS_TCP`.

## 5. Export PLCopen XML : ne jamais casser le projet CODESYS

Ces règles viennent d'essais réels. Chaque erreur citée est arrivée.

### 5.1 Objets libres, jamais d'Application

- **Pas d'Application dans le XML.** Un XML qui contient l'objet `Application` (ou un device, une tâche, une librairie) **écrase toute l'Application** à l'import : configuration, devices, mapping. Le générateur exporte uniquement des objets libres : DUT, GVL, POU, structure de dossiers.
- **Import côté Adel** : clic droit sur Application → Import PLCopenXML. CODESYS propose de remplacer les objets existants (même nom, même ObjectId).
- **Modèles à suivre** : `03_plc/examples/` contient deux exports partiels importés avec succès.

### 5.2 Squelette (ordre imposé par le schéma)

```xml
<?xml version="1.0" encoding="utf-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0200">
  <fileHeader .../>                      <!-- recopié du dernier export CODESYS d'Adel -->
  <contentHeader ...>...</contentHeader> <!-- recopié aussi (coordinateInfo obligatoire) -->
  <types>
    <dataTypes>...</dataTypes>           <!-- toujours présent, même vide -->
    <pous>...</pous>                     <!-- toujours présent, même vide -->
  </types>
  <instances><configurations/></instances>
  <addData>
    <data name="http://www.3s-software.com/plcopenxml/globalvars" handleUnknown="implementation">
      <globalVars name="GVL_COM">...</globalVars>   <!-- un bloc data par GVL -->
    </data>
    <data name="http://www.3s-software.com/plcopenxml/projectstructure" handleUnknown="discard">
      <ProjectStructure>...</ProjectStructure>
    </data>
  </addData>
</project>
```

L'absence de `<dataTypes/>` devant `<pous>` est une erreur de schéma déjà rencontrée.

### 5.3 Règles de détail

- **Espace de noms** : `http://www.plcopen.org/xml/tc6_0200`, en espace de noms par défaut.
- **Commentaires et code** : dans `<xhtml xmlns="http://www.w3.org/1999/xhtml">`, en espace de noms par défaut. Avec un préfixe `html:`, l'élément n'est pas reconnu.
- **Ordre dans `variable`** : `type`, `initialValue`, `addData`, `documentation`.
- **Ordre dans `pou`** : `interface` (`returnType`, `inputVars`, `outputVars`, `inOutVars`, `localVars`, sans bloc vide), puis `body/ST/xhtml`, puis `addData`.
- **Types** :
  - élémentaire : `<BOOL/>` ;
  - dérivé : `<derived name="DUT_..."/>` ;
  - tableau : `<array><dimension lower="0" upper="87"/><baseType><WORD/></baseType></array>`.
- **Valeur initiale** :
  - simple : `<initialValue><simpleValue value="350"/></initialValue>` ;
  - structure : `<initialValue><structValue><value member="X"><simpleValue value="3.75"/></value></structValue></initialValue>`.
- **`qualified_only`** : à mettre dans `globalVars/addData`, sous la forme `<data name="http://www.3s-software.com/plcopenxml/attributes" handleUnknown="implementation"><Attributes><Attribute Name="qualified_only" Value=""/></Attributes></data>`.
- **ObjectId** : chaque objet porte `addData/data[objectid]/ObjectId`, et le même ObjectId figure dans `ProjectStructure`.
  - **Réutilise l'ObjectId du dernier export CODESYS d'Adel** pour un objet existant (recherche par nom).
  - Pour un objet nouveau, génère un UUID stable, par exemple uuid5 du nom.
  - **Stocke les ObjectId en base** et permets de réimporter un export CODESYS pour les rafraîchir.
- **ProjectStructure** :
  - chaque objet exporté apparaît une fois, dans son dossier (voir 2.4 et le programme de référence) ;
  - on n'y met que les objets exportés ;
  - pas de dossier vide.
- **Construction du XML** : avec une bibliothèque XML (échappement automatique), jamais par concaténation de chaînes.
- **Version CODESYS** : elle normalise les REAL (`350.0` → `350`). Compare des valeurs, pas du texte.

### 5.4 Pièges ST déjà rencontrés

- Chaque affectation se termine par `;`.
- Comparaison sur un WORD : passe par une conversion (`WORD_TO_UINT(x) >= 1`).
- `INT` n'est pas un nom de variable valide.
- Une fonction affecte sa valeur de retour (`GFC_EXC := TRUE;`).
- Accès aux GVL toujours qualifié (`GVL_COM.COM_DIP1500_1...`).
- Une variable de `GVL_MODBUS` utilisée dans le code doit exister dans `GVL_MODBUS`. Ajouter une variable à `GVL_MODBUS` demande aussi un mapping manuel dans l'arbre des devices, à signaler.

### 5.5 Export partiel : n'exporter que ce qui est nécessaire

L'import CODESYS remplace les objets reçus et ne touche pas aux autres. Un export partiel contient donc **l'objet demandé et tout ce qui en dépend et a changé**. Il ne contient rien d'autre, pour ne pas écraser du travail fait directement dans CODESYS.

Règles de dépendance :

| Changement | Objets à exporter |
|---|---|
| Nouveau type | 5 DUT (3 pour une UPI) + FB du type |
| Type modifié | DUT modifiés + FB s'il a changé |
| Nouvelle instance d'équipement | `GVL_COM`, `GVL_EXC`, `GVL_IO`, `GVL_PA` (objets entiers), `GFB_INST` ; `GFC_COM_IN/OUT` si elle a des adresses TCP ou RTU ; `GFC_IO_IN/OUT` si elle a des voies ; `GFC_EXC` et les `FC_EXC_...` nouvelles si elle a des échanges ; le type s'il n'existe pas encore dans le projet |
| Nouvelle UPI | `GVL_COM`, `GVL_EXC`, `GFB_UPI`, `GFC_COM_IN/OUT` si adresses, `GFC_EXC` + FC si échanges |
| Échange ajouté ou modifié | `FC_EXC_<A>_<B>` concernée + `GFC_EXC` |
| Adresse TCP ou RTU modifiée | `GFC_COM_IN/OUT` |
| Voie d'E/S modifiée | `GFC_IO_IN/OUT` (et `GVL_IOMAP` si une variable y est ajoutée, avec mapping manuel à signaler) |
| Valeur PA d'instance | `GVL_PA` |
| `PLC_PRG` | seulement si l'ordre ou la liste des appels change |

- **Export complet** : tous les objets du programme, sauf `DUT_Union_Real_Word`.
- **Dépendances transitives** : si un objet exporté référence un type absent du dernier export CODESYS connu, ce type part aussi.

**Avant chaque export**, affiche un résumé :
1. objets inclus, avec le motif de chaque inclusion ;
2. **objets à supprimer à la main** dans CODESYS (l'import ne supprime jamais rien : type supprimé, objet renommé, ancienne GFC remplacée) ;
3. actions manuelles : taille des zones du serveur Modbus TCP, mapping de nouvelles variables `GVL_MODBUS` / `GVL_IOMAP`, remappage de triggers, nouvelles cartes ;
4. avertissement si `GVL_PA` ou un `DUT_PA` change. Les variables persistantes concernées reprennent leurs valeurs de déclaration au téléchargement suivant.

### 5.6 GVL_PA : objet Persistent Variables

Format observé dans le dernier export CODESYS :
- `<globalVars name="GVL_PA" retain="true" persistent="true">` ;
- chaque variable porte `<Attribute Name="order_in_persistent_editor" Value="n"/>` (0, 1, 2... dans l'ordre) dans son `addData` ;
- la GVL porte `qualified_only`, plus `checksumnoinit_override` et `init_related_code` (attributs internes de CODESYS).

Règle pour ces deux derniers attributs :
- **liste des variables inchangée** depuis le dernier export : recopie-les ;
- **liste changée** : ne les mets pas. **Ce cas n'est pas encore testé** : fais valider le premier import de ce type par Adel sur une copie de son projet.

Valeur d'instance différente du défaut : `structValue` sur la variable (voir 5.3). Elle n'a jamais été importée dans CODESYS, donc à faire valider au premier cas réel.

### 5.7 Zones Modbus TCP

`GVL_MODBUS` dimensionne les tableaux TCP à 1504 bits (`ARRAY[0..187] OF BYTE`) et 1501 registres (`ARRAY[0..1500] OF WORD`). Le serveur Modbus TCP du device doit être au moins aussi grand. Si une adresse dépasse, signale-le et donne la nouvelle taille à régler.

## 6. Contenu du dossier `03_plc`

| Fichier | Contenu |
|---|---|
| `Gaia_V6_export_CODESYS.xml` | **dernier export brut fait par CODESYS** : format exact, en-têtes, dossiers, ObjectId, `GVL_PA` persistante |
| `Gaia_V7_reference_full.xml` | **programme actuel complet** = export V6 + V7 (`FC_EXC_*`, `GFC_EXC`, `PLC_PRG` modifié, sans `GFC_EXC_IN/OUT`). C'est la cible de l'export complet |
| `examples/Gaia_V6_partial_PA_DIP1500.xml` | export partiel : 2 DUT + 3 FB modifiés. **Importé avec succès** |
| `examples/Gaia_V7_partial_FC_EXC.xml` | export partiel : 9 FC, `GFC_EXC`, `PLC_PRG`, avec un nouveau dossier `FC_EXC` |
| `examples/*.st` | listings ST correspondants, pour lecture |

Les deux XML complets passent la validation du schéma `06_schema/tc6_xml_v201.xsd`. Ce schéma est en tc6_0201 : remplace `tc6_0200` par `tc6_0201` dans une copie avant de valider.

## 7. Points encore ouverts

- **ADM100W** : adresse esclave.
- **Ligne RTU** : parité, bits de données, bits de stop, timeout, port physique du PLC.
- **Coils DIP1500** : canal d'écriture cyclique ou sur trigger.
- **DOSENSOR_1** : voies câblées réelles.
- **Adresses IP** de FUXA et du PV800.
- **Export de `GVL_PA` modifiée** (5.6).
- **Renommage par instance des variables RTU de `GVL_MODBUS`** (2.7).
- **Coquille de commentaire dans le programme** : `DUT_EXC_DOSENSOR.UPI_INPUT_GLOBAL_LOCAL_WEBVISU` porte le commentaire "global HMI local active".

Le générateur accepte ces valeurs vides ou "TO CHECK" sans bloquer.
