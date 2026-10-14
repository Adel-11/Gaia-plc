# Format du CSV d'import/export FUXA (devices + tags)

Source de vérité : `02_fuxa_reference/SCADA_V1-devices_1_export_original.csv` (export réel de FUXA)
et `02_fuxa_reference/SCADA_V1-devices_complet_attendu.csv` (fichier complété, validé à la main).
Le script `reference_gen_csv.py` montre comment le fichier attendu a été produit à partir de l'IO List.

Un caractère de trop et l'import FUXA échoue. Ces règles sont strictes.

## Encodage et lignes

- UTF-8 **sans BOM**.
- Fins de ligne **LF** (`\n`) uniquement, jamais CRLF.
- Chaque ligne de données (`@header`, `D@`, `T@`) se termine par une **virgule finale** avant le `\n`.
- Le fichier se termine par `\n\n\n` (dernière ligne de tag + 2 lignes vides), comme l'export d'origine.
- Pas de guillemets, pas d'espace autour des virgules.

## Structure du fichier (dans cet ordre)

1. Ligne 1 : `!! CSV separator property convertion to "~"` (orthographe d'origine, à garder telle quelle).
2. Bloc de commentaires devices (`#id,: Device id, GUID` ...), recopié à l'identique de l'export.
3. Ligne vide.
4. `@header,id,name,enabled,type,polling,property.address,...` (en-tête devices).
5. Lignes `D@` : une par device. Le device `0` (FUXA Server) existe toujours.
6. Ligne vide.
7. Bloc de commentaires tags (`#deviceId,:Reference to device` ...), recopié à l'identique.
8. Ligne vide.
9. `@header,deviceId,id,name,label,type,memaddress,address,divisor,init,format,direction,edge,description,sysType,options,daq.enabled,daq.interval,daq.changed,daq.restored,`
10. Tag système du device 0 (`PLC Connection Status`), recopié tel quel.
11. Ligne vide.
12. Lignes `T@` des tags du device Modbus.
13. Deux lignes vides finales.

Le plus sûr : garder les blocs fixes (commentaires, en-têtes, devices, tag système) comme gabarit
recopié octet par octet depuis le dernier export FUXA importé, et ne générer que les lignes `T@`.

## Ligne device Modbus TCP (exemple réel)

```
D@,d_abeaaea7-b8914e5b,PLC,true,ModbusTCP,500,192.168.0.10,9600,TcpPort,8,10,false,None,,,10,,1,
```
Champs : id, name, enabled, type, polling (ms), address (IP), baudrate, connectionOption, databits,
delay, forceFC16, parity, port (vide = défaut), rack, slaveid, slot, stopbits.

## Ligne tag (exactement 20 virgules)

```
T@,<deviceId>,<tagId>,<name>,<label>,<type>,<memaddress>,<address>,<divisor>,,,,,,,,<daq.enabled>,<daq.interval>,<daq.changed>,<daq.restored>,
```
Exemple réel :
```
T@,d_abeaaea7-b8914e5b,t_e524c270-b63c44e2,DO SENSOR SIGNAL,,Float32MLE,300000,89,1,,,,,,,,false,60,false,false,
```
Valeurs par défaut utilisées : label vide, divisor `1`, champs init/format/direction/edge/description/sysType/options vides,
`false,60,false,false` pour le DAQ.

### tagId
- Format : `t_` + 8 hexa minuscules + `-` + 8 hexa minuscules (ex. `t_e524c270-b63c44e2`).
- Unique dans le fichier. **Stable** : un tag déjà connu garde son id d'une génération à l'autre
  (stocker l'id en base, le récupérer à l'import d'un CSV FUXA).

### name
- C'est le nom affiché dans FUXA. Unique dans le device.
- Interdits : virgule, `~`, guillemets, retour ligne, espace en début/fin.

### memaddress (chaîne de 6 caractères)
| Table Modbus | memaddress |
|---|---|
| Coils (FC1/5/15) | `000000` |
| Discrete Inputs (FC2) | `100000` |
| Input Registers (FC4) | `300000` |
| Holding Registers (FC3/6/16) | `400000` |

### address
Index dans la table, celui de la colonne « Modbus Address » de l'IO List, **sans** le préfixe de table :
- Coils : Modbus Address telle quelle (1, 2, 3...).
- Discrete Inputs : Modbus Address - 10000 (10001 donne 1).
- Input / Holding Registers : Modbus Address telle quelle (1, 2, 3...).
- Variable 32 bits : un seul tag, à l'adresse du **premier** des deux registres.

### type (noms exacts FUXA, sensibles à la casse)
`Bool, Int16, UInt16, Int32, UInt32, Float32, Float64, Int64, Int16LE, UInt16LE, Int32LE, UInt32LE, Float32LE,
Float64LE, Int64LE, Int32MLE, UInt32MLE, Float32MLE, Float64MLE, String`
(liste lue dans `server/runtime/devices/modbus/datatypes.js` du dépôt FUXA)

Correspondance utilisée côté automate CODESYS (le PLC est serveur Modbus TCP, ses REAL sont exposés en CDAB) :
| Type CODESYS | Type FUXA |
|---|---|
| BOOL | `Bool` |
| WORD / UINT | `UInt16` |
| INT | `Int16` |
| REAL | `Float32MLE` |
| DWORD / UDINT | `UInt32MLE` |
| DINT | `Int32MLE` |

La correspondance doit être une table de configuration modifiable, pas codée en dur.

### divisor
FUXA divise la valeur lue par `divisor` (et multiplie à l'écriture). Pas d'offset possible.
Une échelle avec offset (ex. `(valeur - 500) / 10`) ne peut pas être portée par le CSV : la laisser à 1
et la documenter dans l'Excel.

## Points non vérifiés

- Comportement de FUXA à l'import quand un tagId existe déjà (mise à jour ou doublon). À tester avant de s'y fier.
- Version de FUXA installée sur le Pi : non relevée.
