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
| `scowl-2020-names` | names | HPND | 2026-10-27 | en | https://github.com/en-wl/wordlist/blob/master/scowl/Copyright | English proper names (people, places, brands) from SCOWL's proper-names lists: every name up to level 80, and names from levels 81-95 unless they are frequent lowercase words (50+ lowercase occurrences in the en Tatoeba sentences). |
| `wikidata-given-es` | names | CC0-1.0 | 2026-10-27 | es | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the es region, via the QLever endpoint. |
| `wikidata-given-en` | names | CC0-1.0 | 2026-10-27 | en | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the en region, via the QLever endpoint. |
| `wikidata-given-ca` | names | CC0-1.0 | 2026-10-27 | ca | https://www.wikidata.org/wiki/Wikidata:Licensing | Given names (P735) of Wikidata people citizens of the ca region, via the QLever endpoint. |
| `wikidata-family-es` | names | CC0-1.0 | 2026-10-27 | es | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the es region, via the QLever endpoint. |
| `wikidata-family-en` | names | CC0-1.0 | 2026-10-27 | en | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the en region, via the QLever endpoint. |
| `wikidata-family-ca` | names | CC0-1.0 | 2026-10-27 | ca | https://www.wikidata.org/wiki/Wikidata:Licensing | Family names (P734) of Wikidata people citizens of the ca region, via the QLever endpoint. |
| `wikidata-brands` | brands | CC0-1.0 | 2026-10-27 | ca, en, es | https://www.wikidata.org/wiki/Wikidata:Licensing | Brands and companies with 10+ Wikipedia sitelinks (Wikidata via QLever). |
| `wikidata-orgs-catalan` | brands | CC0-1.0 | 2026-10-28 | ca, es | https://www.wikidata.org/wiki/Wikidata:Licensing | Companies, businesses, brands and organisations (P31/P279* of Q4830453, Q783794, Q431289 or Q43229) headquartered (P159) or located (P131) in Catalonia, the Valencian Community, the Balearic Islands, Andorra or Pyrenees-Orientales, or with country Andorra; every sitelink count; single-token ca, es, en and mul labels (Wikidata via QLever). |
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
  - LICENSE.txt and README metadata 'License: CC BY 4.0' (the README's older 'GNU license' paragraph is superseded; changelog v2.8: 'The license changed to CC BY 4.0'). Original data: AnCora 3.0.1 Spanish, https://doi.org/10.5281/zenodo.4710748, CC BY 4.0.
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-train.conllu> (sha256 `edfeabcddf81e65ed3b647ed31117a7739df5e10336c3e707ff68217b0ef18c5`)
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-dev.conllu> (sha256 `29fe512fdd86724d2c9f24604001f2ca021dd64b06c9fd0a9ad9ee7a7de05e01`)
- `ud-ancora-ca`: <https://raw.githubusercontent.com/UniversalDependencies/UD_Catalan-AnCora/r2.18/ca_ancora-ud-test.conllu> (sha256 `7cd23807c1f8b090959b82fcab566c6312fc048c29bc9db5ff0bf794c9b98633`)
  - LICENSE.txt and README metadata 'License: CC BY 4.0' (the README's older 'GNU license' paragraph is superseded; changelog v2.8: 'The license changed to CC BY 4.0'). Original data: AnCora Catalan 2.0.0, https://doi.org/10.5281/zenodo.4762030, CC BY 4.0.
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
- `wikidata-orgs-catalan`: <https://qlever.dev/api/wikidata?query=PREFIX+wd%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fentity%2F%3E+PREFIX+wdt%3A+%3Chttp%3A%2F%2Fwww.wikidata.org%2Fprop%2Fdirec…> (sha256 `832cdac0a3dea5d6186a61e25e8e27c533acb9a968d11fd0995b28e7a2c78aae`)
  - Licensing page: 'All data in Wikidata has a CC0 license'. QLever results follow Wikidata, so the pinned sha256 is the copy downloaded on the check date.
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

## Tuning decisions

- Name-list thresholds (ratified by the controller): single-token entries only, and inside a hyphenated or apostrophised name only capitalised parts of 3+ letters, because multi-word labels ('De la Fuente', 'La Coruna', 'The ... Company') and their particles would remove function words. Wikidata names count when borne by at least ceil(region people / 70,000) notable people (es 5, en 16, ca 3; region sizes counted on the check date), so the share is the same in each region and one-off labels such as 'De', 'Para' or 'He' do not remove common words. US Census surnames count from 20,000 bearers (about the top 1,700): lower tiers include 'EL', 'PARA', 'DE' and 'THE'. SCOWL proper names: all levels, with frequent lowercase words exempt above level 80 (see the SCOWL decision below). GeoNames: 1000+ inhabitants strictly (cities1000 also lists admin seats with fewer) and no PPLX neighbourhoods. These are rules over whole lists, chosen while measuring the tune split; no single entry was added or removed.
- Abbreviations: 'PO' was taken out of the reviewed list after a tune leak ('PO-55102' marked as an id). It is excluded by class, as an identifier prefix; the masker also treats a letter-prefixed id as one {id} token.
- Exemptions (I1 ruling): words in function-<lang>.txt (closed-class words and common adverbs), actions-<lang>.txt (the recipe action catalogue with inflections; es and en) and, for en, the [dates] section (months and weekdays) are kept even when a name, place or brand list carries them. They pass only in lowercase, so a capitalised form mid-sentence is still masked. With every such class kept, the holdout split leaked one marked token in en; dropping the class of auxiliaries, modals and contraction parts from function-en.txt brought it back to zero. The class went as a whole; no single entry was edited.
- Business nouns (I1 follow-up): in es the reviewed business-es.txt list is exempt too, so words such as 'reunion' (also a place) and 'manana' pass; tune and holdout both stayed at zero leaks. In en the same exemption leaked one marked token on the tune split, so en keeps subtracting business words that a name list carries ('send', 'list', 'forward' and 'wait' still pass through actions-en.txt).
- Protected top-N (I1 ruling): the N most frequent base words are kept even when a name list carries them. N was chosen on the tune split (1000 gave the best readability with zero tune leaks) and then lowered because the holdout split leaked: es to 300 (0 holdout leaks at 300, 1 at 600), en to 0 (any N from 50 up leaked 1 on holdout). Holdout results were read as counts only.
- Catalan (addendum 2026-10-28): the ca tune split leaked one lowercase regional brand that is also a common word. The fix is a source class, never an entry: wikidata-orgs-catalan, every company, business, brand or organisation headquartered or located in Catalonia, the Valencian Community, the Balearic Islands, Andorra or Pyrenees-Orientales, at any sitelink count, single-token labels only. It is subtracted from ca and from es (es tune and holdout readability unchanged, zero leaks). Idescat name statistics were not added: their API terms of use are not an allowed licence (no modification, attribution in Idescat's own form), so they are rejected as INE was.
- Catalan protections (same mechanism as es): business-ca.txt is exempt (tune 0.918 -> 0.931, zero leaks on tune and holdout). Protected top-N was scanned on the tune split over 0, 100, 300, 600, 1000 and 2000: 600 and more leaked one marked given name on tune, so N=300 was chosen first. The holdout2 split then leaked one top-300 word that a given-name and surname list carries, so ca uses no top-N protection (N=0; tune 0.933 -> 0.931): the class went as a whole, no entry was edited. The same holdout2 run leaked two Catalan date words; the masker now joins a weekday to its date across a comma or 'dia', reads the numeral 'u' (vint-i-u, l'u) and elided numbers (l'onze), and treats l'octubre like d'octubre.
- SCOWL proper names (addendum 2026-10-28, fix round 1): levels up to 80 are subtracted in full, as before. Levels 81-95 are also subtracted, because a capitalised surname that is also a common word leaked at a sentence start on the holdout2 split, and those tiers carry it (so this choice partly fits holdout2). Taken whole, levels 81-95 removed about 670 base words that are ordinary English (time, work, see, say, morning, market, okay, save, 've) and cut en readability from 0.865 to 0.851 (tune) and from 0.839 to 0.824 (holdout). So a word from levels 81-95 is not subtracted when it occurs at least 50 times in lowercase in the en Tatoeba sentences (frequent_exempt). The threshold was chosen on the tune split only: 1000, 300, 100, 50, 20, 10 and 5 were scanned, and 50 is the highest that brings tune back to 0.865. Holdout was then read as counts: 0 leaks, 0.839. About 310 of the 670 words come back; about 360 rarer ones stay subtracted. Level 95 also lists 'The' and 'If', which are kept as function words.

## Languages

- `ca`: ships (base size 25000). Suffix clitics ('l, -ho...) are read by the masker: at most one apostrophe clitic and two hyphen clitics after a stem that is itself in the lexicon, either one word or one word after a one-letter prefix elision (d'enviar-lo, d'escriure'l). Fixed pronoun combinations (n'hi, s'ho, li'n, se'n...) pass as whole words; after a hyphen one counts as one hyphen and one apostrophe clitic (posar-s'hi).
- `en`: ships (base size 25000). The holdout2 split has one known leak: a frequent lowercase English word used as a surname, capitalised at a sentence start (kept by the SCOWL frequent-word exemption; recorded in the corpus gate).
- `es`: ships (base size 25000). The holdout2 split has one known leak: a lowercase common Spanish word used as a client's brand or name, which no permissive name, place, brand or organisation list carries (recorded in the corpus gate).

Hand-written inputs (`business-*`, `numbers-*`, `dates-*`, `currency-*`,
`abbreviations.txt`) are part of this repository, under its MIT licence.

## SCOWL copyright and permission notices

The English lexicon is derived from SCOWL word lists (levels 10-50) and
subtracts its proper-names lists (all levels; above level 80, frequent lowercase words are kept). SCOWL's `Copyright`
file, reproduced in full as its notices require:

```
The collective work is Copyright 2000-2018 by Kevin Atkinson as well
as any of the copyrights mentioned below:

  Copyright 2000-2018 by Kevin Atkinson

  Permission to use, copy, modify, distribute and sell these word
  lists, the associated scripts, the output created from the scripts,
  and its documentation for any purpose is hereby granted without fee,
  provided that the above copyright notice appears in all copies and
  that both that copyright notice and this permission notice appear in
  supporting documentation. Kevin Atkinson makes no representations
  about the suitability of this array for any purpose. It is provided
  "as is" without express or implied warranty.

Alan Beale <biljir@pobox.com> also deserves special credit as he has,
in addition to providing the 12Dicts package and being a major
contributor to the ENABLE word list, given me an incredible amount of
feedback and created a number of special lists (those found in the
Supplement) in order to help improve the overall quality of SCOWL.

The 10 level includes the 1000 most common English words (according to
the Moby (TM) Words II [MWords] package), a subset of the 1000 most
common words on the Internet (again, according to Moby Words II), and
frequently class 16 from Brian Kelk's "UK English Wordlist
with Frequency Classification".

The MWords package was explicitly placed in the public domain:

    The Moby lexicon project is complete and has
    been place into the public domain. Use, sell,
    rework, excerpt and use in any way on any platform.

    Placing this material on internal or public servers is
    also encouraged. The compiler is not aware of any
    export restrictions so freely distribute world-wide.

    You can verify the public domain status by contacting

    Grady Ward
    3449 Martha Ct.
    Arcata, CA  95521-4884

    grady@netcom.com
    grady@northcoast.com

The "UK English Wordlist With Frequency Classification" is also in the
Public Domain:

  Date: Sat, 08 Jul 2000 20:27:21 +0100
  From: Brian Kelk <Brian.Kelk@cl.cam.ac.uk>

  > I was wondering what the copyright status of your "UK English
  > Wordlist With Frequency Classification" word list as it seems to
  > be lacking any copyright notice.

  There were many many sources in total, but any text marked
  "copyright" was avoided. Locally-written documentation was one
  source. An earlier version of the list resided in a filespace called
  PUBLIC on the University mainframe, because it was considered public
  domain.

  Date: Tue, 11 Jul 2000 19:31:34 +0100

  > So are you saying your word list is also in the public domain?

  That is the intention.

The 20 level includes frequency classes 7-15 from Brian's word list.

The 35 level includes frequency classes 2-6 and words appearing in at
least 11 of 12 dictionaries as indicated in the 12Dicts package.  All
words from the 12Dicts package have had likely inflections added via
my inflection database.

The 12Dicts package and Supplement is in the Public Domain.

The WordNet database, which was used in the creation of the
Inflections database, is under the following copyright:

  This software and database is being provided to you, the LICENSEE,
  by Princeton University under the following license.  By obtaining,
  using and/or copying this software and database, you agree that you
  have read, understood, and will comply with these terms and
  conditions.:

  Permission to use, copy, modify and distribute this software and
  database and its documentation for any purpose and without fee or
  royalty is hereby granted, provided that you agree to comply with
  the following copyright notice and statements, including the
  disclaimer, and that the same appear on ALL copies of the software,
  database and documentation, including modifications that you make
  for internal use or for distribution.

  WordNet 1.6 Copyright 1997 by Princeton University.  All rights
  reserved.

  THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON
  UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
  IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON
  UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-
  ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF THE
  LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT INFRINGE ANY
  THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS.

  The name of Princeton University or Princeton may not be used in
  advertising or publicity pertaining to distribution of the software
  and/or database.  Title to copyright in this software, database and
  any associated documentation shall at all times remain with
  Princeton University and LICENSEE agrees to preserve same.

The 40 level includes words from Alan's 3esl list found in version 4.0
of his 12dicts package.  Like his other stuff the 3esl list is also in the
public domain.

The 50 level includes Brian's frequency class 1, words appearing
in at least 5 of 12 of the dictionaries as indicated in the 12Dicts
package, and uppercase words in at least 4 of the previous 12
dictionaries.  A decent number of proper names is also included: The
top 1000 male, female, and Last names from the 1990 Census report; a
list of names sent to me by Alan Beale; and a few names that I added
myself.  Finally a small list of abbreviations not commonly found in
other word lists is included.

The name files form the Census report is a government document which I
don't think can be copyrighted.

The file special-jargon.50 uses common.lst and word.lst from the
"Unofficial Jargon File Word Lists" which is derived from "The Jargon
File".  All of which is in the Public Domain.  This file also contain
a few extra UNIX terms which are found in the file "unix-terms" in the
special/ directory.

The 55 level includes words from Alan's 2of4brif list found in version
4.0 of his 12dicts package.  Like his other stuff the 2of4brif is also
in the public domain.

The 60 level includes all words appearing in at least 2 of the 12
dictionaries as indicated by the 12Dicts package.

The 70 level includes Brian's frequency class 0 and the 74,550 common
dictionary words from the MWords package.  The common dictionary words,
like those from the 12Dicts package, have had all likely inflections
added.  The 70 level also included the 5desk list from version 4.0 of
the 12Dics package which is in the public domain.

The 80 level includes the ENABLE word list, all the lists in the
ENABLE supplement package (except for ABLE), the "UK Advanced Cryptics
Dictionary" (UKACD), the list of signature words from the YAWL package,
and the 10,196 places list from the MWords package.

The ENABLE package, mainted by M\Cooper <thegrendel@theriver.com>,
is in the Public Domain:

  The ENABLE master word list, WORD.LST, is herewith formally released
  into the Public Domain. Anyone is free to use it or distribute it in
  any manner they see fit. No fee or registration is required for its
  use nor are "contributions" solicited (if you feel you absolutely
  must contribute something for your own peace of mind, the authors of
  the ENABLE list ask that you make a donation on their behalf to your
  favorite charity). This word list is our gift to the Scrabble
  community, as an alternate to "official" word lists. Game designers
  may feel free to incorporate the WORD.LST into their games. Please
  mention the source and credit us as originators of the list. Note
  that if you, as a game designer, use the WORD.LST in your product,
  you may still copyright and protect your product, but you may *not*
  legally copyright or in any way restrict redistribution of the
  WORD.LST portion of your product. This *may* under law restrict your
  rights to restrict your users' rights, but that is only fair.

UKACD, by J Ross Beresford <ross@bryson.demon.co.uk>, is under the
following copyright:

  Copyright (c) J Ross Beresford 1993-1999. All Rights Reserved.

  The following restriction is placed on the use of this publication:
  if The UK Advanced Cryptics Dictionary is used in a software package
  or redistributed in any form, the copyright notice must be
  prominently displayed and the text of this document must be included
  verbatim.

  There are no other restrictions: I would like to see the list
  distributed as widely as possible.

The 95 level includes the 354,984 single words, 256,772 compound
words, 4,946 female names and the 3,897 male names, and 21,986 names
from the MWords package, ABLE.LST from the ENABLE Supplement, and some
additional words found in my part-of-speech database that were not
found anywhere else.

Accent information was taken from UKACD.

The VarCon package was used to create the American, British, Canadian,
and Australian word list.  It is under the following copyright:

  Copyright 2000-2016 by Kevin Atkinson

  Permission to use, copy, modify, distribute and sell this array, the
  associated software, and its documentation for any purpose is hereby
  granted without fee, provided that the above copyright notice appears
  in all copies and that both that copyright notice and this permission
  notice appear in supporting documentation. Kevin Atkinson makes no
  representations about the suitability of this array for any
  purpose. It is provided "as is" without express or implied warranty.

  Copyright 2016 by Benjamin Titze

  Permission to use, copy, modify, distribute and sell this array, the
  associated software, and its documentation for any purpose is hereby
  granted without fee, provided that the above copyright notice appears
  in all copies and that both that copyright notice and this permission
  notice appear in supporting documentation. Benjamin Titze makes no
  representations about the suitability of this array for any
  purpose. It is provided "as is" without express or implied warranty.

  Since the original words lists come from the Ispell distribution:

  Copyright 1993, Geoff Kuenning, Granada Hills, CA
  All rights reserved.

  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions
  are met:

  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.
  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  3. All modifications to the source code must be clearly marked as
     such.  Binary redistributions based on modified source code
     must be clearly marked as modified versions in the documentation
     and/or other materials provided with the distribution.
  (clause 4 removed with permission from Geoff Kuenning)
  5. The name of Geoff Kuenning may not be used to endorse or promote
     products derived from this software without specific prior
     written permission.

  THIS SOFTWARE IS PROVIDED BY GEOFF KUENNING AND CONTRIBUTORS ``AS IS'' AND
  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
  ARE DISCLAIMED.  IN NO EVENT SHALL GEOFF KUENNING OR CONTRIBUTORS BE LIABLE
  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
  OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
  HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
  OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
  SUCH DAMAGE.
```
