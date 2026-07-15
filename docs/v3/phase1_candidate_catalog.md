# V3 Real Python Bug Catalog

- Status: `pass`
- Cases: `25`
- Repositories: `6`
- Candidate/accepted/rejected: `20` / `0` / `5`
- Catalog SHA-256: `c53def9c01715dfb89993750351a8c5fe5ab1502b47bda7f6ef83d7c7d175588`
- Import errors: `0`

| Case | Split | Repository | Python | Status | Targeted commands | Ground truth files |
| --- | --- | --- | --- | --- | ---: | ---: |
| bugsinpy-black-1 | development | psf/black | 3.8.3 | rejected | 1 | 1 |
| bugsinpy-black-10 | development | psf/black | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-black-2 | development | psf/black | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-black-3 | development | psf/black | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-black-5 | development | psf/black | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-fastapi-1 | validation | tiangolo/fastapi | 3.8.3 | candidate | 1 | 4 |
| bugsinpy-fastapi-4 | validation | tiangolo/fastapi | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-fastapi-5 | validation | tiangolo/fastapi | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-fastapi-7 | validation | tiangolo/fastapi | 3.8.3 | candidate | 1 | 1 |
| bugsinpy-pysnooper-1 | development | cool-RR/PySnooper | 3.8.1 | candidate | 1 | 2 |
| bugsinpy-pysnooper-2 | development | cool-RR/PySnooper | 3.8.1 | candidate | 1 | 1 |
| bugsinpy-pysnooper-3 | development | cool-RR/PySnooper | 3.8.1 | candidate | 1 | 1 |
| bugsinpy-tornado-2 | test | tornadoweb/tornado | 3.7.0 | rejected | 1 | 1 |
| bugsinpy-tornado-4 | test | tornadoweb/tornado | 3.7.0 | rejected | 2 | 1 |
| bugsinpy-tornado-5 | test | tornadoweb/tornado | 3.7.0 | rejected | 1 | 1 |
| bugsinpy-tornado-9 | test | tornadoweb/tornado | 3.7.0 | rejected | 1 | 1 |
| bugsinpy-tqdm-2 | validation | tqdm/tqdm | 3.6.9 | candidate | 1 | 2 |
| bugsinpy-tqdm-3 | validation | tqdm/tqdm | 3.6.9 | candidate | 1 | 1 |
| bugsinpy-tqdm-4 | validation | tqdm/tqdm | 3.6.9 | candidate | 1 | 1 |
| bugsinpy-tqdm-5 | validation | tqdm/tqdm | 3.6.9 | candidate | 1 | 1 |
| bugsinpy-youtube-dl-2 | test | ytdl-org/youtube-dl | 3.7.0 | candidate | 1 | 1 |
| bugsinpy-youtube-dl-3 | test | ytdl-org/youtube-dl | 3.7.0 | candidate | 1 | 1 |
| bugsinpy-youtube-dl-4 | test | ytdl-org/youtube-dl | 3.7.0 | candidate | 1 | 1 |
| bugsinpy-youtube-dl-6 | test | ytdl-org/youtube-dl | 3.7.0 | candidate | 1 | 1 |
| bugsinpy-youtube-dl-8 | test | ytdl-org/youtube-dl | 3.7.0 | candidate | 1 | 1 |

## Safety Boundary

The importer parses assignment files, test commands, setup scripts, and patches as data. It does not execute benchmark shell scripts or setup commands. Candidate cases become accepted only after independent bug/fix reproduction artifacts exist.
