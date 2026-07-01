# Contributing to OpenGrab

Thanks for your interest! Here's how to contribute:

1. **Set up** your environment:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Linux/macOS
   python -m venv .venv && .venv\Scripts\activate      # Windows
   pip install -e ".[dev]"
   ```
2. **Fork** the repository and create a branch:
   `feature/my-feature` or `fix/bug-description`
3. Make your changes with tests
4. Ensure quality gates pass:
   ```bash
   python scripts/check.py              # ruff + mypy + pytest (no e2e)
   python scripts/check.py --skip-tests # solo ruff + mypy
   pytest -m "not e2e"                  # tests rápidos (unit + integration)
   pytest                               # todos los tests (incluye e2e — requiere red)
   ```
5. Update `CHANGELOG.md` under `[Unreleased]`
6. Open a descriptive Pull Request

## Standards

- **Strict typing**: all code is `mypy --strict` compliant
- **Tests**: unit and integration tests required for new features
- **Commits**: follow [Conventional Commits](https://www.conventionalcommits.org/).
  Examples with Spanish descriptions (the project standard):
  ```
  feat: modo incógnito con secure wipe
  fix: dismiss_job no persistía tras recargar
  refactor(download): extraer _build_ydl_opts a función pura
  ```
- **Architecture**: see [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for
  design decisions and established patterns. When extracting logic from
  large modules, use the **facade pattern** (Fase 2 precedent) — create
  a new module behind a thin wrapper, migrate callsites, then remove the
  wrapper. For isolated logic, prefer **pure functions** that are
  testable without `TestClient` (Fase 1 precedent).
- **Documentation**: keep docs updated when adding features or changing behavior

> [!NOTE]
> Issues and PRs in Spanish are welcome. / Issues y PRs en español son bienvenidos.

---

## Español

### Cómo Contribuir

1. **Configurá** tu entorno:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Linux/macOS
   python -m venv .venv && .venv\Scripts\activate      # Windows
   pip install -e ".[dev]"
   ```
2. **Hacé fork** del repositorio y creá una branch:
   `feature/mi-feature` o `fix/descripcion-del-bug`
3. Hacé tus cambios con tests
4. Asegurate de que pasen los gates:
   ```bash
   python scripts/check.py              # ruff + mypy + pytest (sin e2e)
   python scripts/check.py --skip-tests # solo ruff + mypy
   pytest -m "not e2e"                  # tests rápidos (unit + integration)
   pytest                               # todos los tests (incluye e2e — requiere red)
   ```
5. Actualizá `CHANGELOG.md` en `[Unreleased]`
6. Abrí un Pull Request descriptivo

### Estándares

- **Tipado estricto**: todo el código pasa `mypy --strict`
- **Tests**: unitarios e integración requeridos para nuevas features
- **Commits**: seguí [Conventional Commits](https://www.conventionalcommits.org/).
  Ejemplos con descripción en español (el estándar del proyecto):
  ```
  feat: modo incógnito con secure wipe
  fix: dismiss_job no persistía tras recargar
  refactor(download): extraer _build_ydl_opts a función pura
  ```
- **Arquitectura**: ver [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) para
  decisiones de diseño y patrones establecidos. Al extraer lógica de
  módulos grandes, usá el **patrón facade** (precedente de Fase 2) —
  creá un módulo nuevo detrás de un wrapper fino, migrá los callsites,
  y remové el wrapper. Para lógica aislada, preferí **funciones puras**
  testeables sin `TestClient` (precedente de Fase 1).
- **Documentación**: mantené los docs al día
