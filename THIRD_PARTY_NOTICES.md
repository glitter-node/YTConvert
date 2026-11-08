# Third-party components and release review

This inventory records what was found in the project and its local environment on 2026-11-09. It is not a license for third-party code and does not replace any component's own license text. YTConvert's [MIT license](LICENSE) covers original project code only to the extent Glitter.kr owns or may license it. Media retrieved from third parties, separately installed tools, package dependencies, and externally supplied assets do not become MIT-licensed by being used with YTConvert.

## Distribution boundary

The inspected project source contains application Python, plain HTML/CSS/JavaScript, documentation, tests, and host-specific configuration. `requirements.txt` declares Python packages but does not contain their code. The local `venv/` contains installed packages and license files and is excluded by `.gitignore`; it must not be assumed to be absent from an archive created by copying the entire directory. `/opt/bin/ffmpeg` and `/opt/bin/ffprobe` are outside this project and are invoked as separate executables. No npm manifest, JavaScript package, bundled font file, image file, icon file, FFmpeg binary, or release-packaging script was found in the inspected project tree. The CSS names `Inter` and system fonts but provides no font file or `@font-face` download. UI symbols are text glyphs. YouTube thumbnails are fetched at runtime and are not bundled. The favicon routes refer to host files outside the project; their provenance and license were not verified.

The table below reports installed versions and license declarations from the local Python distribution metadata, cross-checked against available license files under `venv/lib/python3.11/site-packages/`. Version ranges in `requirements.txt` can resolve to different versions later. Every listed Python package is an installed dependency, **not part of the intended source archive**. Direct means named in `requirements.txt`; transitive means installed for those packages; test means needed for local tests. Source/project links come from installed package metadata unless otherwise noted.

| Component | Role | Local version | Verified license declaration | Source or project reference |
| --- | --- | --- | --- | --- |
| FastAPI | Direct API framework | 0.162.2 | MIT; local `LICENSE` | [source](https://github.com/fastapi/fastapi) |
| Uvicorn | Direct ASGI server | 0.54.0 | BSD-3-Clause; local `LICENSE.md` | [source](https://github.com/Kludex/uvicorn) |
| httpx | Direct HTTP test client | 0.28.1 | BSD-3-Clause; local `LICENSE.md` | [source](https://github.com/encode/httpx) |
| pytest | Direct test runner | 9.2.0 | MIT; local `LICENSE` | [source](https://github.com/pytest-dev/pytest) |
| python-dotenv | Direct environment loader | 1.2.4 | BSD-3-Clause; local `LICENSE` | [source](https://github.com/theskumar/python-dotenv) |
| pytz | Direct legacy configuration time zone support | 2023.3.post1 | MIT; local `LICENSE.txt` | [project page in metadata](http://pythonhosted.org/pytz); source repository not verified |
| yt-dlp | Direct metadata extractor and optional media downloader | 2026.10.26 | Unlicense in installed metadata and local `LICENSE`; upstream also identifies bundled MIT and ISC components | [source](https://github.com/yt-dlp/yt-dlp), [upstream third-party licenses](https://github.com/yt-dlp/yt-dlp/blob/master/THIRD_PARTY_LICENSES.txt) |
| annotated-doc | FastAPI transitive | 0.0.5 | MIT; local `LICENSE` | [source](https://github.com/fastapi/annotated-doc) |
| annotated-types | Pydantic transitive | 0.8.0 | MIT; local `LICENSE` | [source](https://github.com/annotated-types/annotated-types) |
| anyio | Async runtime transitive | 4.16.0 | MIT; local `LICENSE` | [project documentation in metadata](https://anyio.readthedocs.io/en/latest/); source repository not verified here |
| certifi | HTTP certificate bundle transitive | 2026.10.23 | MPL-2.0; local `LICENSE` | [source](https://github.com/certifi/python-certifi) |
| click | Uvicorn transitive | 8.5.1 | BSD-3-Clause; local `LICENSE.txt` | [source](https://github.com/pallets/click/) |
| h11 | Uvicorn/httpcore transitive | 0.16.0 | MIT; local `LICENSE.txt` | [source](https://github.com/python-hyper/h11) |
| httpcore | httpx transitive | 1.0.9 | BSD-3-Clause; local `LICENSE.md` | [source](https://github.com/encode/httpcore) |
| idna | HTTP name processing transitive | 3.20 | BSD-3-Clause; local `LICENSE.md` | [source](https://github.com/kjd/idna) |
| opentelemetry-api | FastAPI transitive | 1.45.2 | Apache-2.0; local `LICENSE` | [source](https://github.com/open-telemetry/opentelemetry-python) |
| pydantic | FastAPI transitive | 2.14.3 | MIT; local `LICENSE` | [source](https://github.com/pydantic/pydantic) |
| pydantic-core | Pydantic transitive, compiled extension | 2.50.4 | MIT; local `LICENSE` | [source](https://github.com/pydantic/pydantic/tree/main/pydantic-core) |
| starlette | FastAPI transitive | 1.10.0 | BSD-3-Clause; local `LICENSE.md` | [source](https://github.com/Kludex/starlette) |
| typing-inspection | Pydantic/FastAPI transitive | 0.4.5 | MIT; local `LICENSE` | [source](https://github.com/pydantic/typing-inspection) |
| typing-extensions | Runtime transitive | 4.16.0 | PSF-2.0 in installed metadata; local `LICENSE` | [source](https://github.com/python/typing_extensions) |
| iniconfig | pytest transitive | 2.3.1 | MIT; local `LICENSE` | [source](https://github.com/pytest-dev/iniconfig) |
| packaging | pytest transitive | 26.3 | Apache-2.0 OR BSD-2-Clause; local `LICENSE.APACHE` and `LICENSE.BSD` | [source](https://github.com/pypa/packaging) |
| pluggy | pytest transitive | 1.7.0 | MIT; local `LICENSE` | [source](https://github.com/pytest-dev/pluggy) |
| Pygments | pytest transitive | 2.21.0 | BSD-2-Clause; local `LICENSE` | [source](https://github.com/pygments/pygments) |

The local environment also contains pip, setuptools, and wheel with their own package and vendored-component license files. They are environment/build tools, not application imports or declared project dependencies. A release that includes the virtual environment or build tooling must inventory and preserve those licenses and all bundled component notices separately.

## FFmpeg, ffprobe, and build-linked libraries

The project requires operator-supplied FFmpeg and ffprobe executables at configured absolute paths; it does not bundle them. FFmpeg's [license guidance](https://ffmpeg.org/legal.html) describes LGPL-2.1-or-later as the base and GPL-2.0-or-later when GPL components are enabled. The examined `/opt/bin/ffmpeg` and `/opt/bin/ffprobe` are version 6.1.4; their `-L` output states GPL version 2 or later, and their `-buildconf` output includes `--enable-gpl` and `--enable-libx264`. FFmpeg's [x264 documentation](https://www.ffmpeg.org/general.html#x264) identifies x264 as GPL version 2 or later. This observation applies to these binaries, not to every possible FFmpeg build. The same build reports libopus, libmp3lame, GnuTLS, and other enabled components; their exact binary composition and complete license/notice set were not audited here.

If FFmpeg, ffprobe, their libraries, a container image containing them, or a prebuilt virtual environment are redistributed, the distributor must review the exact binaries and dependency licenses and provide the required license notices, corresponding source or source offers, build information, and other materials required by the applicable licenses. This file alone is not a substitute for those materials. Installing an independently supplied executable on an operator's host is a different distribution situation from bundling that executable with YTConvert.

## Attribution and unresolved checks

When redistributing Python packages, keep each package's own copyright and license text and any notices included in its distribution. MIT and BSD packages require preservation of their notices; Apache-2.0, MPL-2.0, PSF-2.0, and dual-licensed packages require review of their particular terms and, where applicable, notice, source, or modification information. yt-dlp's installed Unlicense metadata does not replace the MIT/ISC notices identified by its upstream third-party notice file. Retain the exact upstream notice files for any packaged version that includes those components. Do not relabel any third-party package or binary as Glitter.kr MIT code.

No preexisting project LICENSE, COPYING, NOTICE, or third-party notice file was found. No copied or modified third-party application source was identified from local headers and filenames, but absence of headers is not proof of origin. The provenance of the host-specific `data/config/_conf_.py` and generated-looking `_conf_.pyi`, any prior contributors' rights, and any externally served favicon assets remains unverified. Confirm these before a public source release. Future dependency versions, optional yt-dlp extras, native wheels, and any new frontend assets need a fresh inventory. Source-code licensing does not grant rights to process third-party media or override platform terms.
