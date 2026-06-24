# Contributing to OpenGrab

Thanks for your interest! Here's how to contribute:

1. **Fork** the repository
2. Create a branch: `feature/my-feature` or `fix/bug-description`
3. Make your changes with tests
4. Ensure quality gates pass:
   ```bash
   ruff check .          # linting
   mypy --strict .       # type checking
   pytest -m "not e2e"   # unit + integration tests
   ```
5. Update `CHANGELOG.md` under `[Unreleased]`
6. Open a descriptive Pull Request

## Standards

- **Strict typing**: all code is `mypy --strict` compliant
- **Tests**: unit and integration tests required for new features
- **Commits**: follow [Conventional Commits](https://www.conventionalcommits.org/)
- **Documentation**: keep docs updated when adding features or changing behavior

> [!NOTE]
> Issues and PRs in Spanish are welcome. / Issues y PRs en español son bienvenidos.

---

## Español

### Cómo Contribuir

1. **Hacé fork** del repositorio
2. Creá una branch: `feature/mi-feature` o `fix/descripcion-del-bug`
3. Hacé tus cambios con tests
4. Asegurate de que pasen los gates:
   ```bash
   ruff check .          # linting
   mypy --strict .       # type checking
   pytest -m "not e2e"   # tests unitarios e integración
   ```
5. Actualizá `CHANGELOG.md` en `[Unreleased]`
6. Abrí un Pull Request descriptivo

### Estándares

- **Tipado estricto**: todo el código pasa `mypy --strict`
- **Tests**: unitarios e integración requeridos para nuevas features
- **Commits**: seguí [Conventional Commits](https://www.conventionalcommits.org/)
- **Documentación**: mantené los docs al día
