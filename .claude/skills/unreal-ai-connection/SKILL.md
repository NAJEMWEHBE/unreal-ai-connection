```markdown
# unreal-ai-connection Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the development conventions and workflows used in the `unreal-ai-connection` repository, a Python codebase designed to facilitate connections between Unreal Engine and AI components. You'll learn about file naming, import/export styles, commit patterns, and how to structure and run tests in this project.

## Coding Conventions

### File Naming
- Use **camelCase** for file names.
  - Example: `aiConnector.py`, `unrealBridge.py`

### Import Style
- Use **relative imports** within the package.
  - Example:
    ```python
    from .utils import parseConfig
    from .models import AIModel
    ```

### Export Style
- Use **named exports** (explicitly declare what is exported from a module).
  - Example:
    ```python
    # aiConnector.py
    def connect():
        pass

    __all__ = ['connect']
    ```

### Commit Patterns
- Use **conventional commit messages**.
- Prefix refactoring commits with `refactor:`
  - Example:
    ```
    refactor: improve connection handling for async workflows
    ```

## Workflows

### Refactoring Code
**Trigger:** When improving code structure or performance without changing external behavior  
**Command:** `/refactor`

1. Identify code sections that need improvement.
2. Refactor the code, ensuring no change in public APIs.
3. Use relative imports and maintain camelCase file naming.
4. Write a commit message starting with `refactor:`.
5. Run tests to ensure no regressions.
6. Push changes and open a pull request.

### Adding a New Module
**Trigger:** When introducing new functionality  
**Command:** `/add-module`

1. Create a new Python file using camelCase naming.
2. Use relative imports for dependencies.
3. Define named exports using `__all__`.
4. Write or update tests as needed.
5. Commit changes with a descriptive message.
6. Push and open a pull request.

## Testing Patterns

- Test files use the pattern `*.test.ts` (TypeScript test files, possibly for frontend or integration testing).
- The specific testing framework is **unknown**; check existing test files for structure.
- Place test files alongside the modules they test or in a dedicated `tests` directory.
- Example test file name: `aiConnector.test.ts`

## Commands
| Command      | Purpose                                         |
|--------------|-------------------------------------------------|
| /refactor    | Start a code refactoring workflow               |
| /add-module  | Add a new module following project conventions  |
```
