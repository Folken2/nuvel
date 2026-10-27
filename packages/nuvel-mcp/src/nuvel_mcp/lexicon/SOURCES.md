# Lexicon sources

Generated from `scripts/lexicon_src/sources.json` by `scripts/build_lexicon.py`.
Every source meets the licence rule: public domain or CC0, permissive OSI
licences, or CC BY without share-alike or non-commercial terms. No CC-BY-SA,
GPL, LGPL or MPL source is used. Each licence was checked at its licence page
on the date given. Name, place and brand lists are only subtracted from the
base lists; none of their entries ships.

| id | kind | licence | checked | languages | licence page | contributes |
|---|---|---|---|---|---|---|
| `ud-ancora-es` | base | CC-BY-4.0 | 2026-10-27 | es | https://github.com/UniversalDependencies/UD_Spanish-AnCora/blob/r2.18/LICENSE.txt | Spanish base: frequency of lowercase, non-PROPN surface forms (UD Spanish-AnCora r2.18). |
| `ud-ancora-ca` | base | CC-BY-4.0 | 2026-10-27 | ca | https://github.com/UniversalDependencies/UD_Catalan-AnCora/blob/r2.18/LICENSE.txt | Catalan base: frequency of lowercase, non-PROPN surface forms (UD Catalan-AnCora r2.18). |
| `tatoeba-spa` | base | CC-BY-2.0-FR | 2026-10-27 | es | https://tatoeba.org/en/downloads | Spanish base: frequency of lowercase words in Tatoeba sentences (everyday and imperative forms). |
| `tatoeba-cat` | base | CC-BY-2.0-FR | 2026-10-27 | ca | https://tatoeba.org/en/downloads | Catalan base: frequency of lowercase words in Tatoeba sentences (everyday and imperative forms). |
| `tatoeba-eng` | base | CC-BY-2.0-FR | 2026-10-27 | en | https://tatoeba.org/en/downloads | English base: frequency of lowercase words in Tatoeba sentences (everyday and imperative forms). |
| `scowl-2020` | base | HPND | 2026-10-27 | en | https://github.com/en-wl/wordlist/blob/master/scowl/Copyright | English base: SCOWL word lists in size-level order (10 = most common). |
| `scowl-2020-names` | names | HPND | 2026-10-27 | en | https://github.com/en-wl/wordlist/blob/master/scowl/Copyright | English proper names (people, places, brands) from SCOWL's proper-names lists up to level 80 (95 is SCOWL's catch-all tier, with entries such as 'The' and 'If'). |
| `wikidata-given-es` | names | CC0-1.0 | 2026-10-27 | es | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the es region, via the QLever endpoint. |
| `wikidata-given-en` | names | CC0-1.0 | 2026-10-27 | en | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the en region, via the QLever endpoint. |
| `wikidata-given-ca` | names | CC0-1.0 | 2026-10-27 | ca | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the ca region, via the QLever endpoint. |
| `wikidata-family-es` | names | CC0-1.0 | 2026-10-27 | es | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the es region, via the QLever endpoint. |
| `wikidata-family-en` | names | CC0-1.0 | 2026-10-27 | en | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the en region, via the QLever endpoint. |
| `wikidata-family-ca` | names | CC0-1.0 | 2026-10-27 | ca | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the ca region, via the QLever endpoint. |
| `wikidata-brands` | brands | CC0-1.0 | 2026-10-27 | ca, en, es | https://www.wikidata.org/wiki/Wikidata:Licensing | Brands and companies with 10+ Wikipedia sitelinks (Wikidata via QLever). |
| `geonames-cities1000` | places | CC-BY-4.0 | 2026-10-27 | ca, en, es | https://download.geonames.org/export/dump/readme.txt | Populated places with 1000+ inhabitants (not neighbourhoods, PPLX) in the language's countries; name and ASCII name. |
| `geonames-countries` | places | CC-BY-4.0 | 2026-10-27 | ca, en, es | https://download.geonames.org/export/dump/readme.txt | Country and capital names. |
| `census-2010-surnames` | names | public-domain | 2026-10-27 | en | https://www.census.gov/topics/population/genealogy/data/2010_surnames.html | US 2010 Census surnames borne by 20,000+ people (about the top 1,700). |
| `bcn-cognoms` | names | CC-BY-4.0 | 2026-10-27 | ca, es | https://opendata-ajuntament.barcelona.cat/data/dataset/pad_m_cognom | Surnames of Barcelona residents (Ajuntament de Barcelona open data, 2026). |
| `bcn-noms` | names | CC-BY-4.0 | 2026-10-27 | ca, es | https://opendata-ajuntament.barcelona.cat/data/dataset/pad_m_nom_sexe | Given names of Barcelona residents (Ajuntament de Barcelona open data, 2026). |

## Files

Long query URLs are shortened here; `sources.json` has them in full.

- `ud-ancora-es`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Spanish-AnCora/r2.18/es_ancora-ud-train.conllu> (sha256 `47b6fc49d6ed48a016e7fa5ad9f5c7ecf3ec21e9db005df3ea14d10f07c6deb6`)
- `ud-ancora-es`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Spanish-AnCora/r2.18/es_ancora-ud-dev.conllu> (sha256 `1a132068062edf2654fb45b41cd4c59ba7e5c18e7c5f642ed5a9cc351fd9f0fe`)
- `ud-ancora-es`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Spanish-AnCora/r2.18/es_ancora-ud-test.conllu> (sha256 `679f1c05ca654aa26801c80319df45f8f97606718fb534b91755b48f3e67fa6f`)
  - README: 'License: CC BY 4.0' (relicensed from GNU, zenodo 10.5281/zenodo.4762030).
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-train.conllu> (sha256 `edfeabcddf81e65ed3b647ed31117a7739df5e10336c3e707ff68217b0ef18c5`)
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-dev.conllu> (sha256 `29fe512fdd86724d2c9f24604001f2ca021dd64b06c9fd0a9ad9ee7a7de05e01`)
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-test.conllu> (sha256 `7cd23807c1f8b090959b82fcab566c6312fc048c29bc9db5ff0bf794c9b98633`)
  - README: 'License: CC BY 4.0' (relicensed from GNU, zenodo 10.5281/zenodo.4762030).
- `tatoeba-spa`: <https://downloads.tatoeba.org/exports/per_language/spa/spa_sentences.tsv.bz2> (sha256 `29efd66854f11ad3480720ceda6e7b42563d40df4e660d9b0721c56a61753e9a`)
  - Downloads page: 'These files are released under CC BY 2.0 FR' (some sentences also CC0). The export is refreshed weekly, so the pinned sha256 is the copy downloaded on the check date.
- `tatoeba-cat`: <https://downloads.tatoeba.org/exports/per_language/cat/cat_sentences.tsv.bz2> (sha256 `64ca30ee5f39e302293d030f89a6a503b15d309806a4c813d5ad30a4e2d8c593`)
  - Downloads page: 'These files are released under CC BY 2.0 FR' (some sentences also CC0). The export is refreshed weekly, so the pinned sha256 is the copy downloaded on the check date.
- `tatoeba-eng`: <https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2> (sha256 `f634eaaf26623c2a2e170be2dae54b18388479311ae8619d04889df4e5d021f3`)
  - Downloads page: 'These files are released under CC BY 2.0 FR' (some sentences also CC0). The export is refreshed weekly, so the pinned sha256 is the copy downloaded on the check date.
- `scowl-2020`: <https://downloads.sourceforge.net/project/wordlist/SCOWL/2020.12.07/scowl-2020.12.07.tar.gz> (sha256 `5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc`)
  - Copyright file: permission notice in the HPND form; components public domain (Moby, 12Dicts, Kelk) or WordNet's permissive licence.
- `scowl-2020-names`: <https://downloads.sourceforge.net/project/wordlist/SCOWL/2020.12.07/scowl-2020.12.07.tar.gz> (sha256 `5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc`)
- `wikidata-given-es`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `7164673ff9d0730b4ed28608a30433cbc2ba3ce58390f819ba63020122b10e65`)
- `wikidata-given-en`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `0a33eb2a7c508f0825dbad401be1b36b62918a007904a0b0201f0ae0bfb7e092`)
- `wikidata-given-ca`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `4fbc3724a82bb31d423dd4f89dfc3649d247d97ed1a2b77eb2a713e699049b5b`)
- `wikidata-family-es`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `86c17bfc0abf7083209ae87fa50fcee36a5eefc4f15d255bb8fd1eb96b6c0f2f`)
- `wikidata-family-en`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `ad96e99350cd8628efc8427008357d6e905c5d2488362f6d21453382855698b0`)
- `wikidata-family-ca`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `76bc51a63f05d295ae49093958b424f5947fa3d84bf98404852a4e549a0509d8`)
- `wikidata-brands`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `b1729f5e25b544f71562a559eaf126c4a7b241bcaccd71b2a92aecac2be43437`)
- `geonames-cities1000`: <https://download.geonames.org/export/dump/cities1000.zip> (sha256 `da28572d617c58a98636a561476c5d850f89f2b36996fef3395357bec7ff1f7a`)
- `geonames-countries`: <https://download.geonames.org/export/dump/countryInfo.txt> (sha256 `c55f638cfaafb4fe45a9aa15120a0f1f78e8a34a8e1847d7b9320cf740fbc9ab`)
- `census-2010-surnames`: <https://www2.census.gov/topics/genealogy/2010surnames/names.zip> (sha256 `117c41cb4668727b7627b2845b6df3f83eb2a22a1813f42c0ff4bdcab86de135`)
  - US federal government work (17 U.S.C. 105).
- `bcn-cognoms`: <https://opendata-ajuntament.barcelona.cat/data/dataset/48eb3444-4e01-44db-951e-a7a0c6b49089/resource/a11e2f13-9c6f-4e4a-b1f0-93b50312dac5/download> (sha256 `43f8df2b35f7ace61744a747d0d55857244e6b65f814477e5dd29b79d5fe4afa`)
- `bcn-noms`: <https://opendata-ajuntament.barcelona.cat/data/dataset/d002bb3f-7414-4151-9052-773bf4630ad2/resource/319aa0ad-40db-44fa-bb2c-f39f71374326/download> (sha256 `e35390f40f63c436c81c3a63e8ca55d3c73f2c17273e51fb4581e5015ab731a6`)

## Attribution

- SCOWL (scowl-2020): Copyright 2000-2018 by Kevin Atkinson. Permission to use, copy, modify, distribute and sell these word lists, the associated scripts, the output created from the scripts, and its documentation for any purpose is hereby granted without fee, provided that the above copyright notice appears in all copies and that both that copyright notice and this permission notice appear in supporting documentation. Kevin Atkinson makes no representations about the suitability of this array for any purpose. It is provided "as is" without express or implied warranty.
- Universal Dependencies Spanish-AnCora and Catalan-AnCora (r2.18), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.
- Tatoeba (https://tatoeba.org) sentences, CC BY 2.0 FR, https://creativecommons.org/licenses/by/2.0/fr/.
- GeoNames (https://www.geonames.org), CC BY 4.0.
- Ajuntament de Barcelona, Open Data BCN (https://opendata-ajuntament.barcelona.cat), CC BY 4.0.
- Changes: the lexicons hold only folded single words derived from these sources (frequency ranks, minus name, place and brand entries); no source text is reproduced.

## Languages

- `ca`: structure only. With every permissive source above, the tune split still leaks one marked token: a lowercase brand that is also a common Catalan word, which no permissive name, place or brand list contains; adding it by hand is not allowed. Inputs are kept so the language can be switched on when a source is found.
- `en`: ships (base size 25000).
- `es`: ships (base size 25000).

Hand-written inputs (`business-*`, `numbers-*`, `dates-*`, `currency-*`,
`abbreviations.txt`) are part of this repository, under its MIT licence.
