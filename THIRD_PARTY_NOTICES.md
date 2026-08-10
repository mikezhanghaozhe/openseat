# Third-Party Notices

This project includes or depends on third-party software. Their licenses and copyright notices
are reproduced below.

Obligations differ by how the software is used:

- **Dependency** — installed from a package registry, no source copied into this repository. If a
  distributed artifact (Docker image, bundled frontend) contains the dependency, the notice below
  travels with it.
- **Vendored / adapted** — source copied or adapted into this repository. The original license and
  copyright are retained in the affected files and directories.
- **Architectural reference** — no code used. Credited in `README.md`; no license obligation.

---

## Dependencies

| Software | Version | License | Source |
|---|---|---|---|
| PokerKit | 0.7.4 | MIT | https://github.com/uoftcprg/pokerkit |
| FastAPI | — | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | — | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic | — | MIT | https://github.com/pydantic/pydantic |
| React | — | MIT | https://github.com/facebook/react |
| Vite | — | MIT | https://github.com/vitejs/vite |

Regenerate this table before release:

```
pip-licenses --format=markdown --with-urls --with-license-file
npx license-checker --production --markdown
```

---

## Vendored or adapted source

*None at this time.*

If source is ever copied or adapted into this repository, record it here and add a header to every
affected file:

```python
# Portions adapted from <Project> (<url>)
# Copyright (c) <year> <holder>
# Licensed under the MIT License. See THIRD_PARTY_NOTICES.md.
# Modifications (c) 2026 <your name>.
```

---

## Architectural references

No code from these projects is included. Credited for the ideas.

- **TextArena** — https://github.com/LeonGuertler/TextArena (MIT). Reference for the
  environment/agent API shape and the bring-your-own-agent client model.
- **boardgame.io** — https://github.com/boardgameio/boardgame.io (MIT). Reference for the
  server-authoritative reducer with per-player view filtering.

---

## Full license texts

### PokerKit — MIT License

Copyright (c) 2023 University of Toronto Computer Poker Research Group

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

---

> Verify each project's `LICENSE` file directly before adding it here. A repository with no
> LICENSE file is "all rights reserved" — its code may not be copied regardless of how public it
> is.
