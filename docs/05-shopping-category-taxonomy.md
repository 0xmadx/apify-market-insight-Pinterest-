# Pinterest Shopping — COMPLETE CATEGORY TAXONOMY (API reference)

> Source: `/ads/v4/trends/shopping/product_categories` (call once, cache).
> **383 categories across 4 levels.** IDs are stable and region-independent.

## How the hierarchy works

```
L1  VERTICAL          e.g. 1042  Beauty            <- NOT returned by the API (see warning)
 └ L2  MAIN CATEGORY  e.g. 1030  Bath & body
    └ L3  SUB         e.g. 1061  Body care
       └ L4  SUB-SUB  e.g. 1064  Body moisturizers
```

Each record: `{ friendly_name, level, parent_product_category_id, children[], l2_product_category_ids[] }`

### ⚠️ Three things that will bite the code agent

1. **L1 verticals are NOT in the response.** Looking up `1042` in `categories` returns
   `undefined`. Only L2/L3/L4 are present (L2=66, L3=190, L4=127). The 14 L1 IDs appear
   only as `parent_product_category_id` values. **Hardcode the L1 table below.**
2. **`top/` and `metrics/` return IDs only, never names** — always join to this taxonomy.
3. **`parent_product_categories` on the `top/` endpoint expects an L1 vertical ID**, not
   an L2/L3. And pass only one (Doc #3 §A4).

## The 14 L1 verticals (hardcode these — API never returns them)

| L1 ID | Vertical | Trend data? | UI chip? |
|-------|----------|-------------|----------|
| `1007` | Animals & pet supplies | — none | yes |
| `1016` | Arts & entertainment | — none | yes |
| `1042` | Beauty | ✅ YES | yes |
| `1148` | DIY | — none | yes |
| `1161` | Electronics | — none | yes |
| `1181` | Fashion | ✅ YES | yes |
| `1194` | Food & beverages | — none | yes |
| `1241` | Hardware | — none | no |
| `1250` | Home decor | ✅ YES | yes |
| `1315` | Media | — none | yes |
| `1436` | Sporting goods | — none | yes |
| `1481` | Toys & games | — none | yes |
| `1489` | Vehicles & parts | — none | yes |
| `1500` | Wedding | — none | yes |

> ⚠️ **CONTRADICTS doc #7 §4.3 — believe §4.3.** This table says only 3 verticals have
> trend data; §4.3 records **7**, with measured row counts: Fashion 19, Home decor 9,
> Beauty 6, and four the UI never shows — DIY 3, Arts & entertainment 2, Wedding 2,
> Media 1. Three of those counts were re-verified live on 2026-08-19 (19/9/6, exact),
> so §4.3 is the better-evidenced side and `src/vocab.py` follows it. The four hidden
> verticals have NOT been re-verified independently; if a `top/` call on 1148/1016/
> 1500/1315 returns 0 rows, this table is right and §4.3 is stale — record which.
>
> Only **Fashion / Home decor / Beauty** appear in the "Top vertical"
> filter. The other 11 exist in the taxonomy and as "All categories" browse chips only.
> `1241` (Plumbing's parent) has no UI chip at all.

## Level counts

| Level | Meaning | Count |
|-------|---------|-------|
| 1 | Vertical | 14 *(not in API response)* |
| 2 | Main category | 66 |
| 3 | Sub-category | 190 |
| 4 | Sub-sub-category | 127 |
| | **Total returned** | **383** |

## Worked example — the chain the UI shows as a breadcrumb

```
1042 Beauty  (L1, hardcoded)
 └ 1030 Bath & body        (L2)
    └ 1061 Body care       (L3)
       └ 1064 Body moisturizers (L4)

1181 Fashion (L1, hardcoded)
 └ 1104 Clothing           (L2)
    └ 1356 Pants           (L3)   <- /shopping/1356/
       └ 1261 Jeans        (L4)
```
To build a breadcrumb: follow `parent_product_category_id` upward until it's not in
`categories` — that last value is the L1 vertical; resolve its name from the table above.

---

## FULL TREE


### `1007` — Animals & pet supplies

- `1365` **Pet supplies** *(L2)*
  - `1057` Bird supplies
  - `1092` Cat supplies
  - `1149` Dog supplies
  - `1363` Pet carriers & crates
  - `1364` Pet collars & harnesses

### `1016` — Arts & entertainment

- `1248` **Hobbies & creative arts** *(L2)*
- `1359` **Party & celebration** *(L2)*
  - `1502` Wedding ceremony decor
  - `1509` Wedding table decor
- `1504` **Wedding decor** *(L2)*
- `1506` **Wedding gifts** *(L2)*
- `1508` **Wedding stationery** *(L2)*

### `1042` — Beauty  ⭐ **has trend data**

- `1030` **Bath & body** *(L2)*
  - `1032` Bath & shower
    - `1065` Body washes
  - `1061` Body care
    - `1064` Body moisturizers
  - `1236` Hand & foot care
    - `1238` Hand soaps & sanitizers
- `1043` **Beauty supplements** *(L2)*
- `1206` **Fragrance** *(L2)*
  - `1142` Deodorants & antiperspirants
  - `1362` Perfumes & colognes
- `1220` **Hair** *(L2)*
  - `1222` Hair care
    - `1232` Hair treatment
    - `1414` Shampoo & conditioner
  - `1223` Hair color
  - `1231` Hair tools
- `1227` **Hair removal** *(L2)*
  - `1391` Razors & shaving tools
- `1306` **Makeup** *(L2)*
  - `1063` Body makeup
  - `1168` Eye makeup
    - `1076` Brow makeup
    - `1169` Eye shadow
    - `1170` Eyeliners
    - `1180` False eyelashes
    - `1311` Mascaras
  - `1176` Face makeup
    - `1060` Blushes & bronzers
    - `1204` Foundations & concealers
    - `1247` Highlighters
    - `1385` Primers & makeup setters
  - `1301` Lip care
    - `1300` Lip balms
  - `1302` Lip makeup
    - `1303` Lipsticks & lip glosses
  - `1309` Makeup tools
- `1323` **Nails** *(L2)*
  - `1319` Nail art
    - `1320` Nail art kit & tools
    - `1322` Nail polishes
  - `1321` Nail care
- `1420` **Skincare** *(L2)*
  - `1178` Facial cleansers
    - `1474` Toners & astringents
  - `1179` Facial moisturizers
    - `1175` Face lotions & creams
    - `1410` Serums & essences
  - `1421` Skincare masks & peels
  - `1451` Sunscreen
  - `1463` Tanning oils & lotions
- `1464` **Teeth whitening** *(L2)*
  - `1465` Teeth whitening tools

### `1148` — DIY

- `1040` **Beads & jewelry making supplies** *(L2)*
- `1124` **Craft adhesives & magnets** *(L2)*
  - `1218` Glues & tapes
  - `1305` Magnets
- `1125` **Craft cutting tools** *(L2)*
- `1151` **Drawing & painting** *(L2)*
  - `1013` Art & craft paints
  - `1014` Art brushes
  - `1167` Erasers
  - `1361` Pens & pencils
- `1254` **Home improvement tools & supplies** *(L2)*
  - `1242` Hardware supplies
    - `1080` Cabinet hardware
  - `1314` Measuring tools & sensors
    - `1313` Measures & rulers
  - `1476` Tool storage & organization
    - `1519` Work benches
  - `1477` Tools
    - `1155` Drills & screwdrivers
    - `1403` Saws
  - `1493` Wall paints
    - `1355` Paint & paint tools
  - `1517` Woodworking materials
    - `1515` Wood boards & planks
    - `1518` Woodworking plans
- `1282` **Knitting & crochet** *(L2)*
  - `1283` Knitting & crochet tools
  - `1468` Thread & yarn
- `1357` **Paper crafts** *(L2)*
  - `1090` Cardstock papers
  - `1112` Coloring books
- `1383` **Pottery & sculpting** *(L2)*
  - `1127` Craft molds
- `1467` **Textile & sewing** *(L2)*
  - `1171` Fabric
  - `1412` Sewing machines
  - `1413` Sewing patterns

### `1161` — Electronics

- `1018` **Audio** *(L2)*
  - `1019` Audio accessories
- `1113` **Communications** *(L2)*
  - `1466` Telephony
- `1162` **Electronics accessories** *(L2)*
  - `1114` Computer accessories

### `1181` — Fashion  ⭐ **has trend data**

- `1003` **Accessories** *(L2)*
  - `1023` Bag & luggage accessories
    - `1268` Keychains
  - `1026` Bandanas
  - `1052` Belts & suspenders
  - `1174` Face coverings
  - `1449` Glasses & sunglasses
  - `1217` Gloves & mittens
  - `1221` Hair accessories
    - `1224` Hair combs
    - `1226` Hair pins, claws & clips
    - `1233` Hair wreaths
    - `1376` Ponytail holders
    - `1470` Tiaras
    - `1490` Veils
    - `1510` Wigs & hair extensions
  - `1240` Handkerchiefs
  - `1246` Headwear
    - `1243` Hats
  - `1286` Lanyards
  - `1325` Neckties
  - `1358` Parasols & rain umbrellas
  - `1369` Pinback buttons
  - `1402` Sashes
  - `1404` Scarves & shawls
  - `1495` Wallets & card cases
- `1024` **Bags & luggage** *(L2)*
  - `1021` Backpacks
  - `1050` Belt bags
  - `1120` Cosmetic & toiletry Bags
  - `1145` Diaper bags
  - `1157` Duffel bags
  - `1239` Handbags
  - `1316` Messenger bags
  - `1417` Shopping totes
  - `1446` Suitcases
- `1104` **Clothing** *(L2)*
  - `1106` Clothing sets
  - `1154` Dresses
  - `1331` One-pieces
    - `1267` Jumpsuits & rompers
    - `1332` Onesies
    - `1353` Overalls
  - `1350` Outerwear
    - `1108` Coats & jackets
  - `1356` Pants
    - `1091` Casual pants
    - `1152` Dress pants
    - `1261` Jeans
    - `1292` Leggings
  - `1418` Shorts
  - `1423` Skirts
  - `1424` Sleepwear & loungewear
  - `1447` Suits
  - `1448` Suits & suit separates
  - `1454` Swimwear
  - `1478` Tops
    - `1059` Blouses
    - `1079` Button down shirts
    - `1452` Sweaters & cardigans
    - `1453` Sweatshirts & hoodies
    - `1455` T-Shirts
    - `1462` Tank tops
  - `1482` Traditional & ceremonial clothing
  - `1485` Uniforms
    - `1437` Sports uniforms
- `1123` **Costumes & accessories** *(L2)*
- `1255` **Hosiery** *(L2)*
  - `1432` Socks & tights
  - `1439` Stockings
- `1262` **Jewelry & watch accessories** *(L2)*
- `1263` **Jewelry & watches** *(L2)*
  - `1008` Anklets
  - `1062` Body jewelry
  - `1071` Bracelets
  - `1075` Brooches & lapel pins
  - `1099` Charms & pendants
  - `1160` Earrings
  - `1265` Jewelry sets
  - `1324` Necklaces
  - `1393` Rings
  - `1497` Watches
- `1415` **Shoe accessories** *(L2)*
- `1416` **Shoes** *(L2)*
  - `1069` Boots
  - `1102` Clogs & mules
  - `1187` Flats
  - `1354` Oxfords & loafers
  - `1386` Pumps
  - `1400` Sandals
  - `1426` Slippers
  - `1428` Sneakers & athletic shoes

### `1194` — Food & beverages

- `1054` **Beverages** *(L2)*
- `1198` **Food items** *(L2)*

### `1241` — Hardware

- `1375` **Plumbing** *(L2)*

### `1250` — Home decor  ⭐ **has trend data**

- `1036` **Bathroom accessories** *(L2)*
  - `1035` Bath towels & washcloths
  - `1037` Bathroom accessory sets
  - `1039` Beach towels
  - `1419` Shower curtains
  - `1473` Toilet paper holders
  - `1479` Towel racks & holders
- `1046` **Bedding** *(L2)*
  - `1045` Bed sheets
  - `1058` Blankets
  - `1158` Duvet covers
  - `1368` Pillowcases & shams
  - `1388` Quilts & comforters
- `1183` **Fireplace & wood stove accessories** *(L2)*
- `1184` **Fireplaces** *(L2)*
- `1209` **Furniture** *(L2)*
  - `1048` Beds & accessories
    - `1049` Beds & bed frames
    - `1245` Headboards & footboards
    - `1312` Mattresses
  - `1081` Cabinets & storage furniture
    - `1012` Armoires & wardrobes
    - `1027` Bar carts
    - `1038` Bathroom vanities
    - `1047` Bedroom vanities
    - `1066` Bookcases & standing shelves
    - `1077` Buffets & sideboards
    - `1100` China cabinets & hutches
    - `1153` Dressers
    - `1166` Entertainment centers & TV stands
    - `1443` Storage cabinets & lockers
    - `1494` Wall shelves & ledges
    - `1514` Wine racks
  - `1128` Cribs & toddler beds
  - `1211` Furniture sets
  - `1333` Ottomans
  - `1335` Outdoor furniture
  - `1409` Seating
    - `1053` Benches
    - `1095` Chairs
    - `1434` Sofas
  - `1460` Tables
    - `1002` Accent tables
    - `1144` Desks
    - `1273` Kitchen & dining room tables
    - `1327` Nightstands
- `1249` **Home accessories** *(L2)*
  - `1015` Artificial flora
  - `1017` Artwork
    - `1140` Decorative tapestries
    - `1380` Posters, prints & visual artwork
    - `1407` Sculptures & statues
  - `1029` Baskets
  - `1067` Bookends
  - `1136` Decorative bowls
  - `1138` Decorative jars
  - `1141` Decorative trays
  - `1143` Desk & shelf clocks
  - `1182` Figurines
  - `1251` Home decor decals
  - `1252` Home fragrance accessories
    - `1086` Candle holders
  - `1253` Home fragrances
    - `1089` Candles
    - `1259` Incense
  - `1317` Mirrors
  - `1328` Novelty signs
  - `1367` Picture frames
  - `1408` Seasonal & holiday decorations
  - `1425` Slipcovers & cushions
  - `1469` Throw pillows
  - `1487` Vases
  - `1491` Wall clocks
  - `1496` Wallpapers
  - `1512` Window treatments
    - `1131` Curtain & drape rods
  - `1513` Window treatments
    - `1133` Curtains & drapes
    - `1511` Window blinds & shades
  - `1520` Wreaths & garlands
- `1257` **Household appliances** *(L2)*
- `1258` **Household supplies** *(L2)*
  - `1288` Laundry hampers & supplies
  - `1441` Storage & organization
    - `1105` Clothing & closet storage
    - `1366` Photo albums & storage
    - `1444` Storage hooks & racks
- `1270` **Kitchen & dining** *(L2)*
  - `1028` Barware
    - `1107` Coasters
  - `1119` Cookware & bakeware
    - `1025` Bakeware
    - `1118` Cookware
  - `1156` Drinkware
    - `1109` Coffee & tea cups
    - `1438` Stemware
    - `1483` Tumblers & water bottles
  - `1193` Food & beverage carriers
  - `1203` Food storage supplies
    - `1202` Food storage containers
  - `1275` Kitchen appliances
    - `1110` Coffee makers & espresso machines
    - `1147` Dishwashers
    - `1196` Food cookers & steamers
    - `1199` Food mixers & blenders
    - `1352` Ovens & cooktops
  - `1277` Kitchen linens
    - `1009` Aprons
    - `1103` Cloth napkins
    - `1281` Kitchen towels
    - `1371` Placemats
    - `1458` Table runners
    - `1459` Tablecloths
  - `1280` Kitchen tools & utensils
    - `1083` Cake decorating supplies
    - `1116` Cookie cutters
    - `1134` Cutting boards
    - `1276` Kitchen knives
    - `1279` Kitchen organizers
  - `1461` Tableware
    - `1146` Dinnerware
    - `1188` Flatware
    - `1411` Serveware
- `1289` **Lawn & garden** *(L2)*
  - `1214` Gardening
    - `1381` Pots & planters
  - `1216` Gardening tools
  - `1290` Lawn & garden decor
    - `1186` Flags & windsocks
    - `1205` Fountains & ponds
    - `1291` Lawn ornaments & garden sculptures
  - `1339` Outdoor living
    - `1336` Outdoor furniture sets
    - `1338` Outdoor grills
    - `1345` Outdoor seating
    - `1346` Outdoor structures
    - `1347` Outdoor tables
  - `1373` Plants
  - `1499` Watering & irrigation
- `1295` **Lighting** *(L2)*
  - `1285` Lamps
    - `1190` Floor lamps
    - `1457` Table lamps
  - `1297` Lighting fixtures
    - `1097` Chandeliers
    - `1360` Pendant lights
    - `1492` Wall light fixtures
  - `1326` Night lights & ambient lighting
- `1296` **Lighting accessories** *(L2)*
  - `1284` Lamp shades
- `1378` **Pool & spa accessories** *(L2)*
- `1397` **Rugs** *(L2)*
  - `1010` Area rugs
  - `1033` Bath mats & rugs
  - `1150` Door mats
  - `1278` Kitchen mats
  - `1344` Outdoor rugs
  - `1398` Runner rugs

### `1315` — Media

- `1068` **Books** *(L2)*
- `1159` **DVDs & videos** *(L2)*
- `1304` **Magazines & newspapers** *(L2)*
- `1318` **Music & sound recordings** *(L2)*

### `1436` — Sporting goods

- `1260` **Indoor games** *(L2)*
- `1343` **Outdoor recreation** *(L2)*
  - `1085` Camping & hiking
  - `1135` Cycling
  - `1185` Fishing
  - `1337` Outdoor games

### `1481` — Toys & games

- `1212` **Games** *(L2)*
- `1340` **Outdoor play equipment** *(L2)*
- `1387` **Puzzles** *(L2)*
- `1480` **Toys** *(L2)*

### `1489` — Vehicles & parts

- `1488` **Vehicle parts & accessories** *(L2)*

### `1500` — Wedding

- `1165` **Engagement & wedding rings** *(L2)*
- `1503` **Wedding clothing** *(L2)*
  - `1074` Bridesmaid dresses
  - `1191` Flower girl dresses
  - `1219` Groom & groomsmen suits
  - `1505` Wedding dress
