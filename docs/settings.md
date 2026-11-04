# A szimuláció beállításai

Mit jelent a **Simulation** fül minden beállítása, mit lehet megadni, és mit változtat az eredményen (1.2.3-as verzió).
English version: [`settings_en.md`](settings_en.md). A felhasználói útmutató: [`user_guide.md`](user_guide.md).

A szimuláció a megadott rendszert a referencia-cellára skálázza (átlagterhelés 10,788 W), azon futtatja a
szabályozókat, majd az eredményt visszaskálázza a te mértékegységeidre. A skálázás egyetlen szorzó: 10,788 W osztva a
feltöltött terhelés átlagával. Az akku, a teljesítménykorlát, a napelem és a szél ugyanezzel a szorzóval skálázódik.
Ezért **csak az arányok számítanak**: kétszer akkora terhelés kétszer akkora akkuval ugyanazt az eredményt adja,
kétszeres költséggel.

## 1. Terhelési adat (Load data)

| Beállítás | Mit jelent | Mire hat |
|---|---|---|
| **Choose File** | A terhelési idősor CSV-ben: időbélyeg és terhelés, 15, 30 vagy 60 perces lépésekben, legalább egy hét. | Ez a szimuláció terhelése. Az átlaga adja a skálázás szorzóját és az alapértelmezett méreteket. |
| **Values are** | Mit tartalmaz a második oszlop: átlagteljesítmény kW-ban vagy W-ban, vagy az intervallum energiája kWh-ban. | Csak az átváltást. Rossz választásnál a terhelés nagyságrendje rossz lesz; ezt az ellenőrzés kiírt átlaga és csúcsa elárulja. |
| **Timestamps (without offset) in** | Az időzóna, amelyben az UTC-eltolás nélküli időbélyegek vannak. | Hogy a terhelés melyik órára esik az időjáráshoz és az árhoz képest. Eltolással (`+01:00`) írt időbélyegeknél nincs hatása. |
| **Check file** | Beolvassa és ellenőrzi a fájlt. | Hiba esetén megáll; figyelmeztetést (nullák, kiugrások, hiányok) kiír, de továbbenged. 2 óránál rövidebb hiányokat interpolál. |
| **Use the reference data set** | A csomagban lévő mért üzemi terhelés (a cikk adata) a cikk rendszerméreteivel. | Referencia-futás: a riport összeveti az eredményt a tárolttal (Reference check). Ugyanez történik, ha a `reference_load_profile_2025-26.csv`-t változatlanul töltöd fel. |

## 2. Rendszer (System)

Az alapértékek a referencia-rendszer arányai a feltöltött terhelés átlagához (**Reference ratios** gomb állítja vissza).

| Beállítás | Mit jelent | Alapérték | Mire hat |
|---|---|---|---|
| **Battery capacity (kWh)** | Az akku teljes energiakapacitása. Ebből a 10–90 %-os SoC-sáv, azaz 80 % használható. | az átlagterhelés 6,67 órája | Nagyobb akku: több eltolható energia, nagyobb megtakarítás, ritkábban éri el a sáv szélét (kevesebb védelmi óra). Az r_E fordítottan arányos vele, így nagy akkunál kisebb a bizonytalanság-kezelés haszna. |
| **Battery power limit (kW)** | A legnagyobb töltő- és kisütő-teljesítmény (mindkettőre ugyanaz). | az átlagterhelés 2,22-szerese | Kisebb korlát: lassabban tud tölteni és kisütni, kevesebb megtakarítás. B-ben az akkuhoz jutó előrejelzési hibát is ez korlátozza (r_E). |
| **PV (kWp)** | Napelem-csúcsteljesítmény. A termelés a referencia-telephely időjárásából jön (é. sz. 48,09°, 35°-os déli dőlés), a referencia-napelemhez arányosan skálázva. | az átlagterhelés 2,78-szorosa | Több napelem: kevesebb vásárolt energia, több többlet, amit az akku eltol vagy le kell vágni. Az előrejelzési hiba is arányosan nő, így az r_E is. |
| **Wind turbine, rated (kW)** | A szélturbina névleges teljesítménye (indulás 1 m/s, névleges 8 m/s, leállás 12 m/s). | az átlagterhelés 1,86-szorosa | Mint a napelemnél: több szél kevesebb vásárolt energiát és arányosan nagyobb előrejelzési hibát (r_E) jelent. 0-ra állítva nincs szél. |

### Bekötések (Configurations)

Egy futásban több is kiválasztható; mindegyik külön fut, ugyanazzal a terheléssel és mérettel.

| Bekötés | Hogyan működik | Hova kerül az előrejelzési hiba | Mit várhatsz |
|---|---|---|---|
| **A – renewable buffer** | A megújuló csak az akkut tölti; a terhelést az akku és a hálózat látja el. A fel nem vett megújuló elveszik. | A hiány csökkenti a tervezett töltést, tehát eléri az akkut. | Több megújuló vész el (levágás), mert csak az akkun át jut a terheléshez; akku nélkül (Grid only) az összes. |
| **B – self-consumption** | A megújuló közvetlenül a terhelést látja el; a többletet és a hiányt az akku veszi fel. | Mindkét irányú eltérés az akkut terheli. | Itt hat leginkább a bizonytalanság-kezelés (DET-BO, UA-MPC): a cikkben itt voltak különbségek a szabályozók között. |
| **C – set-point** | Az akku a tervezett teljesítményt követi, az eltérést a hálózat egyenlíti ki. | A hálózat; az akkuhoz nem jut (r_E = 0). | A szabályozók gyakorlatilag azonosak; a bizonytalanság-kezelés nem hoz hasznot. Csak DET és UA-MPC fut, DET-BO nincs. |

### Szabályozók (Controllers)

Mindegyik óránként újratervez 24 órás horizonttal, a referencia-cellára hangolt súlyokkal. A **Grid only**
(akku nélkül) mindig lefut, ehhez képest számolja a megtakarítást.

| Szabályozó | Hogyan kezeli a bizonytalanságot | Mire számíts |
|---|---|---|
| **DET** | Sehogy: csak az előrejelzés várható értékével tervez. | Ha az előrejelzés téved, a SoC könnyebben eléri a sáv szélét. A B-re hangolt változat ezt erős SoC-középre húzással ellensúlyozza, ami kevesebb arbitrázst, így több költséget jelent. |
| **DET-BO (reference back-off)** | Állandó tartalék (b) a SoC-sáv mindkét szélén, a referencia-hangolás értékével: A-ban b = 0,045, B-ben b = 0,030. | Ha a te r_E-d nagyobb, mint amire b-t méretezték (Γ > 1), több védelmi óra; ha kisebb, fölöslegesen óvatos. |
| **DET-BO re-sized from r_E** | Ugyanez, de b-t minden hónap elején az előző hónap r_E-jéből számolja: b = r_E · 0,8 / 0,75 (Γ = 0,75 cél). Az első hónapban a hónap előtti időszak r_E-jét használja. | Követi az évszakos változást; a cikk téli tesztjében kevesebb védelmi órát kért, mint az UA-MPC, kb. 0,7 %-kal drágábban. |
| **UA-MPC** | A tartalékot a kalibrált előrejelzési szórásból számolja minden lépésre (esélykorlát); a szórást minden hónapban újrakalibrálja. | Nem kell méretezni; a cikkben B-ben 2,9–3,3 %-kal olcsóbb volt a DET-nél. Ha az előrejelzési hibák farka vastagabb a Gauss-feltevésnél (például télen), a tartaléka kevés lehet. |

## 3. Időszak és mód (Period and mode)

| Beállítás | Mit jelent | Mire hat |
|---|---|---|
| **quick** | A szabályozók lefutnak. | Teljes eredmény: költség, védelmi órák, ábrák, minden lépés és MPC-kör. Egy bekötés egy hónapja szabályozónként kb. 15–60 perc. |
| **screening** | Szabályozó nem fut, csak az r_E és a Γ hónaponként és bekötésenként. | Percek alatt megmondja, érdemes-e a bizonytalanság-kezeléssel foglalkozni (r_E ≈ 0 vagy Γ < 0,75 esetén aligha). |
| **months, From / To** | A szimulált naptári hónapok, 2025 októbere és 2026 szeptembere között; egymás után futnak, a SoC átöröklődik. | Minden hónap előtt újratanítja az előrejelző modellt az addigi időjáráson (hónaponként egyszer, 1–3 perc; utána tárolja). Több hónap: arányosan hosszabb futás. |
| **test period Sep 2026** | 2026. szeptember 2–29., a nyári előrejelző modellekkel (a cikk végső tesztje). | A `models_summer` mappa kell hozzá. Referencia-adattal a cikk végső tesztjének tárolt eredményeivel vethető össze (A, B, C). |
| **Days the load data do not cover: fill from the profile** | Ha a fájl nem fedi a szimulált napot, a terhelést az adatban szereplő, az évben legközelebbi nap ugyanazon hétköznapjáról és negyedórájáról veszi. | Egy rövid (akár egyhetes) fájl is meghajthat egész évet; a szezonális változást csak annyira tükrözi, amennyire a fájl tartalmazza. Munkaszüneti napokat nem kezel külön. |
| **leave out** | A szimulált időszakot az adat első és utolsó napjára szűkíti. | Csak a valódi adaton számol. Ha a kiválasztott hónapok egyikét sem fedi a fájl, a futás hibával leáll. |
| **Initial SoC** | Az akku töltöttsége az első hónap elején (0,1–0,9). | Az első napok költségét befolyásolja; hosszabb futásnál elhanyagolható. A referencia-összevetéshez 0,5 kell. |
| **Name** | A futás neve a listában. | Csak a megjelenítést. |
| **Add to queue** | A futást a sor végére teszi. | Ha semmi sem fut, azonnal indul; különben megvárja az előtte lévőket. |

## 4. Rögzített beállítások (nem állíthatók a felületen)

| Beállítás | Érték | Megjegyzés |
|---|---|---|
| SoC-sáv | 10–90 % | Ennek szélén lép közbe a BMS (védelmi óra). |
| Akku-hatásfok | 0,95 töltéskor és kisütéskor | |
| Akku-kopás | 20 €/MWh az átvitt energiára (töltés + kisütés) | A nettó költség része. |
| Időlépés, horizont, újratervezés | 15 perc, 24 óra (96 lépés), óránként | |
| Tarifa | magyar másnapi ár (ENTSO-E), csak vásárlás | Betáplálásért nincs bevétel, hálózati díj, adó, teljesítménydíj nincs. A fel nem használható többlet levágásra kerül. |
| Időjárás, előrejelzés | a referencia-telephelyé, 2025–2026 | Csak a terhelés és a méretek a tieid. |
| Szabályozó-súlyok | a referencia-cellára hangolva | Nincsenek újrahangolva a te rendszeredre; hangolással a sorrend változhat. |
| Párhuzamos futások | 1 (környezeti változó: `OWN_PARALLEL`) | Egy futáson belül a szabályozók párhuzamosan futnak a magokon. |

## 5. Gyors tanácsok

- **Először screeningt futtass** az érdekes hónapokra: ha az r_E ≈ 0 (például C-ben), a szabályozók között nem lesz különbség.
- **Egy hónap, egy bekötés** az első quick futásra; ha jó, bővítsd.
- **Méret-összehasonlításhoz** használd a **Use these settings** gombot egy kész futáson, írd át az akkut, és tedd sorba.
- **A cikk eredményeinek visszaadásához** lásd az útmutató 5. fejezetét: referencia-adat, leave out, kezdő SoC 0,5.
