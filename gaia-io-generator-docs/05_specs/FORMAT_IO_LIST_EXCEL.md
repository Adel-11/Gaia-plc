# Format de l'IO List Excel (table d'échange Modbus)

Source de vérité : `01_io_list_reference/GAIA-ELEC-1451-V1 - IO List.xlsm`.
L'application doit savoir **lire** ce format (import) et le **produire** (export .xlsx, même structure).
Les macros VBA du fichier d'origine ne sont pas à reproduire.

## Onglets

1. `Navigation` : sommaire. Colonne B = liens hypertexte internes vers chaque onglet (`'Nom onglet'!A1`),
   regroupés par dossier (ligne avec icône 📁 en A et nom du groupe en B : `SCADA (général)`, puis un groupe par type d'équipement).
2. Quatre onglets SCADA (échange PLC <-> FUXA via le serveur Modbus TCP du PLC) :
   `SCADA-Coils`, `SCADA-Discrete Inputs`, `SCADA-Input Registers`, `SCADA-Holding Registers`.
3. Pour chaque type d'équipement, un onglet par table Modbus réellement utilisée (côté équipement, PLC maître) :
   `ADM100W-Input Registers`, `ADM100W-Coils`, `DIP1500-Coils`, `DIP1500-Input Registers`, `DIP1500-Holding Registers`.
   (Le lien `DIP1500-Discrete Inputs` de la page Navigation pointe vers un onglet qui n'existe pas : ne pas créer d'onglet vide.)

Chaque onglet de données contient un **tableau Excel structuré** (ListObject) qui commence en A1, style `TableStyleLight14`,
puis une colonne vide, puis un lien `◄ Navigation` en ligne 1 qui renvoie vers `'Navigation'!A1`.
Noms des tableaux : `SCADA_COILS`, `SCADA_DISCRETE_INPUTS`, `SCADA_INPUT_REGISTERS`, `SCADA_HOLDING_REGISTERS`,
`ADM100W_IR`, `ADM100W_COIL`, `DIP1500_COIL`, `DIP1500_IR`, `DIP1500_HR` (schéma : `<EQUIP>_<COIL|DI|IR|HR>`).

## Onglets SCADA : colonnes

| Colonne | Contenu |
|---|---|
| PLC Variable name | Chemin complet de la variable CODESYS, ex. `GVL_SCADA.SCADA_DIP1500_1.INPUT_MOTOR_SPEED` |
| Scada variable name | Nom du tag FUXA, ex. `DIP1500 Motor Speed`. Pour le 2e registre d'un 32 bits : `<nom> (2nd register)` |
| PLC Table | Repère dans le mapping CODESYS du serveur Modbus. Bits : `Coil TCP HMI <octet>.<bit>` (index 0 = `0.0`, index 8 = `1.0`). Registres : `Input Register TCP HMI <index>` (index à partir de 0) |
| Modbus Address | Coils : index+1. Discrete Inputs : 10001 + index. Input/Holding Registers : index+1 |
| FUXA Address | Coils : index+1. Discrete Inputs : 100001 + index. Input/Holding : index+1 |
| HMI Address | Vide pour l'instant (réservé au pupitre Weintek) |
| Com | Liste : `Plc To SCADA`, `SCADA To Plc`, `Two Ways` |
| FUXA / HMI | Liste : `FUXA`, `HMI` |
| Type | Type CODESYS : `BOOL`, `WORD`, `REAL`... |
| Échelle | Texte, ex. `1`, `(valeur - 500) / 10` |
| Unité | Texte, ex. `V`, `rpm`, `°C` |

Une variable 32 bits occupe **deux lignes consécutives** (même PLC Variable name, deux index qui se suivent).
Les tableaux SCADA sont pré-remplis jusqu'à 504 bits / 501 registres : les lignes non affectées gardent
PLC Table et adresses, avec les colonnes de nom vides. Conserver ce principe (adresses libres visibles).

Mise en forme conditionnelle sur toute la largeur du tableau, selon la colonne `Com` :
`SEARCH("scada to plc")`, `SEARCH("plc to scada")`, `SEARCH("two ways")`, chacune avec sa couleur (reprendre les couleurs du fichier de référence).
Validation de données (listes déroulantes) sur `Com` et `FUXA / HMI`.

## Onglets équipement : colonnes

| Colonne | Contenu |
|---|---|
| Equipment Identification | Libellé du point, ex. `ADM100W Frequency (MSW)`, `DIP1500 Motor Speed (LSW)` |
| PLC Variable name | Variable CODESYS liée (vide pour l'instant dans le fichier) |
| Scada variable name | Nom SCADA lié (vide pour l'instant) |
| PLC Table | Ex. `Coil ADM100W 0.0`, `Input Register DIP1500 0` |
| Modbus Address | Adresse du registre **telle que dans la datasheet** : décimal pour l'ADM100W (0, 1, 2...), hexadécimal texte pour le DIP1500 (`0x4001`) |
| Com | Liste : `Equipment To Plc`, `Plc To Equipment`, `Two Ways` |
| Type | Ex. `REAL`, `WORD`, `BOOL`, `UDINT / REAL (selon mode)` |
| Échelle | Texte |
| Unité | Texte |

Pour les 32 bits, deux lignes avec suffixe `(MSW)` / `(LSW)`, dans l'ordre du protocole de l'équipement :
ADM100W = MSW puis LSW (ABCD), DIP1500 = LSW puis MSW (CDAB). L'ordre des mots est une propriété du type d'équipement.

## Exigences pour l'export généré

- Fichier `.xlsx` qui s'ouvre dans Excel **sans message de réparation** (tableaux structurés corrects : noms de colonnes uniques
  et identiques aux cellules d'en-tête, `ref` et `autoFilter` cohérents).
- Nom de fichier configurable, par défaut `GAIA-ELEC-1451-V<n> - IO List.xlsx`.
- Bibliothèque conseillée : openpyxl (testée sur ce fichier, elle lit et écrit les tableaux, validations et mises en forme conditionnelles).
