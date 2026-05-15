```markdown
# UnrealClaudeMCP Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the UnrealClaudeMCP repository, a Python codebase with a focus on clear structure and maintainability. You'll learn about file organization, import/export styles, commit message conventions, and how to write and run tests in this environment.

## Coding Conventions

### File Naming
- All files use **kebab-case** (lowercase words separated by hyphens).
  - **Example:**  
    ```
    user-authentication.py
    data-processor.py
    ```

### Import Style
- **Relative imports** are used throughout the codebase.
  - **Example:**
    ```python
    from .utils import parse_config
    from ..models import User
    ```

### Export Style
- **Named exports** are preferred; functions and classes are explicitly exported.
  - **Example:**
    ```python
    def process_data(data):
        ...
    
    class DataProcessor:
        ...
    ```

### Commit Messages
- Follows **conventional commit** style.
- Prefixes like `docs` are used.
- Messages are concise, averaging 65 characters.
  - **Example:**
    ```
    docs: update README with setup instructions
    ```

## Workflows

### Writing Documentation
**Trigger:** When updating or adding documentation.
**Command:** `/write-docs`

1. Make changes or additions to documentation files.
2. Use the `docs:` prefix in your commit message.
3. Commit and push your changes.

### Adding or Modifying Code
**Trigger:** When implementing new features or fixing bugs.
**Command:** `/update-code`

1. Create or update Python files using kebab-case naming.
2. Use relative imports for internal modules.
3. Export functions/classes explicitly.
4. Write a clear, conventional commit message.
5. Commit and push your changes.

### Running Tests
**Trigger:** Before merging or after making changes.
**Command:** `/run-tests`

1. Identify test files (pattern: `*.test.*`).
2. Use the project's preferred method to run tests (framework is unknown; check project documentation or use standard Python test runners).
3. Review test results and fix any failures.

## Testing Patterns

- Test files follow the `*.test.*` naming convention (e.g., `user-authentication.test.py`).
- The specific testing framework is not detected; use standard Python testing tools like `unittest` or `pytest` if unsure.
- Place tests alongside or near the code they test.

**Example test file:**
```python
# user-authentication.test.py

from .user-authentication import authenticate_user

def test_authenticate_user_valid():
    assert authenticate_user('user', 'pass') is True
```

## Commands
| Command        | Purpose                                      |
|----------------|----------------------------------------------|
| /write-docs    | Start a documentation update workflow        |
| /update-code   | Add or modify code following conventions     |
| /run-tests     | Run all tests before merging or releasing    |
```
