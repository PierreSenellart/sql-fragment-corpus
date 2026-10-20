# Sources, licences and attribution

Three sources are redistributed here. `corpus/manifest.jsonl` carries the text of
every query extracted from them, whether it is in the corpus or not, and
`corpus/<source>_scenarios.jsonl` carries the queries in the corpus with their
instances. The counts below are of the redistributed queries. Each source keeps
its own licence. What is ours, namely the instances, the labels, the manifest
and this documentation, is CC BY-SA 4.0 (`LICENSE`), chosen because the Stack
Exchange material requires share-alike and the whole must therefore carry it.
The code in `scripts/` is under the MIT licence (`scripts/LICENSE`): it contains
no source material, and Creative Commons licences are not meant for software.

## SEDE, 9,052 queries

Queries from the SEDE dataset of *Text-to-SQL in the Wild: A Naturally-Occurring
Dataset Based on Stack Exchange Data*, Moshe Hazoom, Vibhor Malik and Ben Bogin,
NLP4Prog workshop at ACL 2021 (arXiv:2106.05006). The files used are
`data/sede/train.jsonl`, `val.jsonl` and `test.jsonl` of
`https://github.com/hirupert/sede`, at commit `2bf2612` (2021-06-25). 7,804 of
the 9,052 queries are in the corpus.

**Two licences apply to different parts.** The dataset as published by its
authors is under the Apache License 2.0, reproduced at
`LICENSE.SEDE.Apache-2.0`, which is what permits redistribution of the
collection. The query text inside it was written by users of the Stack Exchange
Data Explorer and is their content under CC BY-SA 4.0.

`attribution/sede_queryset.tsv` maps each of the 9,052 scenarios to the query set
it came from, with a link to `data.stackexchange.com`. **The author's name is not
available**: the SEDE dataset records the query body, its title, its description
and a query-set identifier, and no author, so the attribution here is to the
query set and not to a person. A reader who needs the author can follow the link.

The queries' own titles are also absent from that file deliberately: many contain
raw newlines and quotation marks, which no standard TSV reader survives.

Cite: Hazoom, Malik and Bogin (2021).

## dba.stackexchange.com, 2,882 queries

SQL taken from posts on `https://dba.stackexchange.com`, from the Stack Exchange
Data Dump of 2024-04-02, as published on the Internet Archive at
`https://archive.org/details/stackexchange`: the file `dba.stackexchange.com.7z`
(MD5 `d0f417b3c3c9210ed32623a071651411`), whose `Posts.xml` holds the site's
243,410 posts up to 2024-03-31. 1,425 of the 2,882 queries are in the corpus.

User contributions to the Stack Exchange network are CC BY-SA 4.0, which permits
redistribution and requires that the source be indicated, that the original post
be linked and that authors be named.

`attribution/dba_posts.tsv` gives, for each of the 2,882 queries, from 1,741
distinct posts: the post identifier, a link to the post, the author's user
identifier and a link to their profile, and the author's name. 2,776 rows have
an author identifier, and their names come from `Users.xml` in the same archive.
The other 106 are posts whose account no longer exists; for those the dump
records a name on the post itself, and that is the name given: for 103 of them
the `userNNNN` placeholder Stack Exchange gives a deleted account. Every row has
a name.

Cite: the individual posts, through the links in that file.

## BEAVER, 208 queries

Queries from BEAVER, an enterprise text-to-SQL benchmark, in the release its
authors call `BEAVER-MAY-2025`: `https://github.com/peterbaile/beaver-may-2025`,
files `dev_dw.json`, `dev_nw.json`, `dev_tables.json` and `dw_join_keys.json`,
unchanged there since 2025-03-01. 206 of the 208 queries are in the corpus. The
authors' latest release, of 2026-05-20 at `https://beaverbench.github.io/`, has
8,000 queries; it is not the one used here.

The licence of that repository is the MIT licence, “Copyright 2025 Beaver-SQL”,
reproduced at `LICENSE.BEAVER.MIT`, which permits redistribution provided the
notice is included.

Cite: Peter Baile Chen, Devin Yang, Weiyue Li, Fabian Wenz, Yi Zhang, Nesime
Tatbul, Michael Cafarella, Çağatay Demiralp, Michael Stonebraker. *BEAVER: An
Enterprise Benchmark for Text-to-SQL.* arXiv:2409.02038, 2024.

## Not redistributed, built by script

Three sources are part of the corpus and are not redistributed here: their terms
do not permit it, or do not clearly permit it. Their queries appear in no file of
this repository, and neither do their rows. They do appear in
`corpus/manifest.jsonl` and in `labels/fragments.jsonl`, by identifier and
without their text, and `scripts/` builds their queries and instances from the
originals. `README.md` gives the commands.

**SQLShare, 3,777 queries.** SQLShare Data Release 1,
`https://uwescience.github.io/sqlshare/data_release.html`, the query log of a
multi-year SQL-as-a-service experiment. The release page states no licence: it
asks that four publications be cited and notes that it covers consenting users
only. With no grant to redistribute, and the rows being research data its users
consented to a research release of, none of it is included here;
`scripts/sqlshare_extract.py` builds it from the release. Its authors ask that
these be cited:

- Shrainik Jain, Dominik Moritz, Daniel Halperin, Bill Howe, Ed Lazowska.
  *SQLShare: Results from a Multi-Year SQL-as-a-Service Experiment.* SIGMOD 2016.
- Shrainik Jain, Bill Howe. *Data Cleaning in the Wild: Reusable Curation Idioms
  from a Multi-Year SQL Workload.* QDB 2013.
- Shrainik Jain, Dominik Moritz, Bill Howe. *High Variety Cloud Databases.*
  IEEE Cloud Data Management Workshop 2016.
- Bill Howe, Francois Ribalet, Sagar Chitnis, E. Virginia Armbrust. *SQLShare:
  Scientific Workflow via Relational View Sharing.* Computing in Science &
  Engineering 15(2), 2013.

**TPC-DS, 103 queries, and TPC-H, 22 queries.** Query texts from the TPC
Benchmark DS and TPC Benchmark H standard specifications. The TPC permits copying
and distributing its material with the copyright notice, the publication title
and its date, and notice that copying is by permission of the Transaction
Processing Performance Council, but incorporating TPC material into a non-TPC
document requires the TPC's written permission, and TPC-DS material is stated to
be intended solely for use in conjunction with the execution of a TPC-DS
benchmark. The specifications are available free of charge from
`https://www.tpc.org/`. TPC Benchmark, TPC-DS and TPC-H are trademarks of the
Transaction Processing Performance Council.
