# Stack Detector API Reference

## Overview

The `stack_detector` module provides framework and language detection for generated projects. It examines project files in priority order and returns structured information about the project's tech stack.

**Module:** `src.orchestrator.stack_detector`
**Zero-dependency:** Does not import anything from `src/orchestrator` core, enabling isolated testing

## Usage

```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

# Detect the stack of a generated project
info = detect_stack(Path("/path/to/generated/project"))

print(f"Framework: {info.framework}")
print(f"Language: {info.language}")
print(f"Dev command: {info.dev_server_command}")
print(f"Base URL: {info.base_url}")
print(f"Has frontend: {info.has_frontend}")
```

## API Reference

### `detect_stack(project_root: Path) -> StackInfo`

Detects the tech stack of a project by examining files in priority order.

**Parameters:**
- `project_root: Path` — Root directory of the generated project

**Returns:**
- `StackInfo` — Immutable dataclass with stack information

**Raises:**
- None — Always returns a valid `StackInfo` (worst case: `framework="unknown"`)

**Detection order:**
1. `package.json` → JavaScript/TypeScript frameworks
2. `pyproject.toml` → Poetry-based Python projects
3. `requirements.txt` → Pip-based Python projects
4. `Gemfile` → Ruby projects
5. `go.mod` → Go projects
6. `architecture.json` → Explicit tech stack from orchestrator artifact
7. Fallback → Scan for common entry files

**Example:**

```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

root = Path("/workspace/generated_project")
info = detect_stack(root)

if info.has_frontend:
    print(f"Start with: {info.dev_server_command}")
    print(f"Visit: {info.base_url}")
else:
    print("Backend-only project; no browser testing")
```

---

## StackInfo Dataclass

```python
@dataclass(frozen=True)
class StackInfo:
    framework: str                 # Framework name
    language: str                  # Primary language
    package_manager: str           # Package manager
    install_command: str           # Install dependencies
    dev_server_command: str        # Start dev server
    dev_server_port: int           # Default port
    base_url: str                  # Full base URL
    health_check_path: str         # Health check endpoint
    has_frontend: bool             # True if UI exists
```

### Field Reference

#### `framework: str`

Short identifier for the detected framework. Common values:

**JavaScript/TypeScript:**
- `"nextjs"` — Next.js
- `"react-vite"` — React + Vite
- `"cra"` — Create React App
- `"vue"` — Vue (Vite)
- `"svelte"` — Svelte
- `"astro"` — Astro
- `"remix"` — Remix
- `"nuxt"` — Nuxt
- `"express"` — Express.js
- `"fastify"` — Fastify
- `"nest"` — NestJS
- `"hapi"` — Hapi

**Python:**
- `"fastapi"` — FastAPI
- `"django"` — Django
- `"flask"` — Flask
- `"tornado"` — Tornado

**Ruby:**
- `"rails"` — Ruby on Rails

**Go:**
- `"gin"` — Gin web framework
- `"echo"` — Echo web framework
- `"chi"` — Chi router
- `"go"` — Generic Go

**Fallback:**
- `"unknown"` — Could not detect framework

#### `language: str`

Primary programming language. Values:

- `"javascript"` — Plain JavaScript (ES6+)
- `"typescript"` — TypeScript
- `"python"` — Python 3.x
- `"ruby"` — Ruby
- `"go"` — Go
- `"unknown"` — Could not detect language

#### `package_manager: str`

Package manager for dependencies. Values:

- `"npm"` — npm (Node Package Manager)
- `"yarn"` — Yarn
- `"pnpm"` — pnpm
- `"pip"` — pip (Python)
- `"poetry"` — Poetry (Python)
- `"bundler"` — Bundler (Ruby)
- `"go"` — go mod (Go)
- `"unknown"` — Could not detect

#### `install_command: str`

Shell command to install all dependencies. Examples:

```
"npm install"
"npm ci"
"yarn install"
"pnpm install"
"pip install -r requirements.txt"
"poetry install"
"bundle install"
"go mod download"
```

Used by `AppTestServer.install_packages()` to set up dependencies before starting the dev server.

#### `dev_server_command: str`

Shell command to start the development server. Examples:

```
"npm run dev"
"npm start"
"yarn dev"
"next dev"
"python manage.py runserver"
"flask run"
"poetry run python manage.py runserver"
"uvicorn main:app --reload"
"bundle exec rails server"
"go run main.go"
```

The harness executes this command in the project root, redirecting the port to a dynamically allocated free port.

#### `dev_server_port: int`

Default port the dev server listens on. Examples:

- `3000` — React/Next.js default
- `5173` — Vite default
- `8000` — Django/FastAPI default
- `3001` — Alternative frontend port
- `8080` — Common alternative

**Note:** The actual port used may differ. The harness finds a free port and sets it via environment variable (e.g., `PORT=3042`).

#### `base_url: str`

Full base URL for the running dev server. Examples:

```
"http://localhost:3000"
"http://127.0.0.1:3000"
"http://localhost:5173"
"http://localhost:8000"
```

Injected into the agent prompt and Playwright config as `baseURL`.

#### `health_check_path: str`

HTTP path used to verify the server is running. Examples:

```
"/"               # Root path (most common)
"/health"         # Dedicated health endpoint
"/api/health"     # API health endpoint
"/status"         # Status endpoint
```

The harness polls this path (GET request) until it returns HTTP 200 within the timeout period.

#### `has_frontend: bool`

Boolean flag indicating whether the project has a browser-facing UI.

- `True` — Frontend app (React, Next.js, Vue, Angular, etc.); browser testing applies
- `False` — Backend-only (API, CLI, service); browser testing skipped gracefully

Used to gate the QA Browser phase. If `False`, the phase is skipped with an informational note.

---

## Detection Chains

### JavaScript/TypeScript (package.json)

```python
def _detect_from_package_json(pkg_data: dict) -> StackInfo | None:
    """Detect framework from package.json dependencies."""
    deps = pkg_data.get("dependencies", {})

    if "next" in deps:
        return StackInfo(
            framework="nextjs",
            language="typescript",  # or "javascript"
            package_manager=inferred_pm,
            install_command="npm install",
            dev_server_command="npm run dev",
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    if "react" in deps and "vite" in deps:
        return StackInfo(framework="react-vite", ...)

    # ... other frameworks
```

**Detected from:**
- `package.json` `dependencies` section
- `package.json` `scripts.dev` or `scripts.start` for commands
- Lock file presence: `package-lock.json` (npm), `yarn.lock` (yarn), `pnpm-lock.yaml` (pnpm)

### Python (pyproject.toml / requirements.txt)

```python
def _detect_from_pyproject_toml(pyproject: dict) -> StackInfo | None:
    """Detect Python framework from pyproject.toml."""
    deps = pyproject.get("dependencies", [])

    if "fastapi" in str(deps):
        return StackInfo(
            framework="fastapi",
            language="python",
            package_manager="poetry",
            install_command="poetry install",
            dev_server_command="poetry run uvicorn main:app --reload",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,  # Typically API-only
        )

    if "django" in str(deps):
        return StackInfo(framework="django", ...)

    # ... other frameworks
```

**Detected from:**
- `pyproject.toml` `project.dependencies`
- `requirements.txt` contents
- Poetry/pip inferred from file presence

### Go (go.mod)

```python
def _detect_from_go_mod(go_mod_text: str) -> StackInfo | None:
    """Detect Go framework from go.mod."""
    if "github.com/gin-gonic/gin" in go_mod_text:
        return StackInfo(
            framework="gin",
            language="go",
            package_manager="go",
            install_command="go mod download",
            dev_server_command="go run main.go",
            dev_server_port=8080,
            base_url="http://localhost:8080",
            health_check_path="/",
            has_frontend=False,  # Go apps are typically API-only
        )

    # ... other frameworks
```

### Architecture Artifact (architecture.json)

If a previous orchestrator phase produced `architecture.json`, it may contain an explicit tech stack declaration:

```json
{
  "tech_stack": {
    "frontend_framework": "nextjs",
    "backend_framework": "fastapi",
    "database": "postgresql"
  }
}
```

The detector can use this as a hint if file-based detection is inconclusive.

### Fallback: Entry File Scan

```python
def _detect_from_entry_files(project_root: Path) -> StackInfo | None:
    """Last-resort: scan for common entry files."""
    if (project_root / "main.py").exists():
        return StackInfo(framework="unknown", language="python", ...)

    if (project_root / "server.js").exists():
        return StackInfo(framework="unknown", language="javascript", ...)

    # Return "unknown" sentinel
    return StackInfo(framework="unknown", language="unknown", ...)
```

---

## Examples

### Example 1: Detect Next.js + TypeScript

```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

project_root = Path("/workspace/generated/nextjs-app")
info = detect_stack(project_root)

assert info.framework == "nextjs"
assert info.language == "typescript"
assert info.dev_server_command == "npm run dev"
assert info.base_url == "http://localhost:3000"
assert info.has_frontend is True
```

### Example 2: Detect FastAPI Backend

```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

project_root = Path("/workspace/generated/fastapi-api")
info = detect_stack(project_root)

assert info.framework == "fastapi"
assert info.language == "python"
assert info.install_command == "pip install -r requirements.txt"
assert info.has_frontend is False  # API-only
```

### Example 3: Graceful Unknown Detection

```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

# Project with unrecognized structure
project_root = Path("/workspace/generated/mystery-app")
info = detect_stack(project_root)

assert info.framework == "unknown"
assert info.language == "unknown"
# Base command is still valid; assumes Node.js default
```

---

## Testing

The `stack_detector` module is designed for isolated unit testing:

```python
import json
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

def test_detect_nextjs(tmp_path):
    """Test Next.js detection from package.json."""
    pkg = {
        "name": "my-app",
        "dependencies": {"next": "^13.0.0", "react": "^18.0.0"},
        "scripts": {"dev": "next dev"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))

    info = detect_stack(tmp_path)

    assert info.framework == "nextjs"
    assert info.language == "typescript"
    assert info.dev_server_command == "npm run dev"
    assert info.has_frontend is True

def test_detect_fastapi(tmp_path):
    """Test FastAPI detection from requirements.txt."""
    (tmp_path / "requirements.txt").write_text("fastapi==0.95.0\nuvicorn==0.21.0\n")

    info = detect_stack(tmp_path)

    assert info.framework == "fastapi"
    assert info.language == "python"
    assert info.has_frontend is False

def test_detect_unknown(tmp_path):
    """Test graceful handling of unknown projects."""
    # Empty directory
    info = detect_stack(tmp_path)

    assert info.framework == "unknown"
    assert info.language == "unknown"
    # Should still return valid install/dev commands
    assert len(info.install_command) > 0
    assert len(info.dev_server_command) > 0
```

---

## Extending Stack Detection

To add support for a new framework:

1. **Add a detection function:**
   ```python
   def _detect_my_framework(data: dict | str) -> StackInfo | None:
       """Detect MyFramework from config file."""
       if "my-framework" in str(data):
           return StackInfo(
               framework="my-framework",
               language="...",
               package_manager="...",
               install_command="...",
               dev_server_command="...",
               dev_server_port=3000,
               base_url="http://localhost:3000",
               health_check_path="/",
               has_frontend=True,
           )
       return None
   ```

2. **Add to detection chain in `detect_stack()`:**
   ```python
   def detect_stack(project_root: Path) -> StackInfo:
       # ... existing detections ...

       # Try MyFramework detection
       my_config = project_root / "my-framework.config.json"
       if my_config.exists():
           result = _detect_my_framework(_read_json(my_config))
           if result:
               return result

       # ... fallback ...
   ```

3. **Add unit test:**
   ```python
   def test_detect_my_framework(tmp_path):
       config = {"framework": "my-framework"}
       (tmp_path / "my-framework.config.json").write_text(json.dumps(config))

       info = detect_stack(tmp_path)

       assert info.framework == "my-framework"
   ```

---

## See Also

- [QA-Browser Phase Feature Guide](./features/QA_BROWSER_PHASE.md)
- [Configuration Guide](./QA_BROWSER_CONFIGURATION.md)
- [AppTestServer API](./APP_SERVER_API.md)
