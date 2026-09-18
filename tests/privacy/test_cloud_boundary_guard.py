"""Programmatic guards: one cloud boundary, and every cloud entry path is covered.

Adding a model call site, feature, or web route without registering it here (and
giving it an intercepted end-to-end test) fails this module.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import textwrap
from pathlib import Path

from docflow.llm.gateway import SYSTEM_PROMPTS, CloudPromptGateway, Feature

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = "docflow/llm/client.py"
GATEWAY = "docflow/llm/gateway.py"
PROVIDER_MODULES = (
    "openai", "anthropic", "ollama", "httpx", "requests", "urllib3", "aiohttp", "groq",
    "mistralai", "cohere", "litellm", "google.generativeai", "google.genai",
    "urllib.request", "http.client", "socket",
)
PROVIDER_CALLS = ("OpenAI", "AsyncOpenAI", "completions", "responses", "embeddings")

GATEWAY_METHODS = {"cluster", "classify", "learn_rule", "test_connection"}
IMPLIED_FEATURES = {"cluster": Feature.CLUSTERING, "learn_rule": Feature.RULE_LEARNING,
                    "test_connection": Feature.CONNECTION_TEST}

# (module, enclosing function, gateway method)
CALL_SITES = {
    ("docflow/clustering/clusterer.py", "_llm_cluster", "cluster"),
    ("docflow/classification/classifier.py", "_model_classify", "classify"),
    ("docflow/config/learner.py", "_learn_rule_from_correction", "learn_rule"),
    ("docflow/web/app.py", "_suggest_filing", "classify"),
    ("docflow/web/app.py", "test_connection", "test_connection"),
    ("docflow/web/app.py", "v1_connection_test", "test_connection"),
}
CLOUD_ROUTES = {
    "/api/unmatched/suggest": Feature.UNMATCHED_SUGGESTION,
    "/api/v1/ask-ai": Feature.ASK_AI,
    "/api/settings/test-connection": Feature.CONNECTION_TEST,
    "/api/v1/connection-test": Feature.CONNECTION_TEST,
}
E2E_TESTS = {
    Feature.CLUSTERING: [
        "tests.privacy.test_cloud_paths_e2e::test_cluster_pages_egress_is_intercepted_and_rehydrated",
        "tests.privacy.test_pipeline_local_only::test_cli_pipeline_shares_one_gateway_per_job",
    ],
    Feature.CLASSIFICATION: [
        "tests.privacy.test_cloud_paths_e2e::test_classify_candidates_rules_md_path_is_intercepted",
        "tests.privacy.test_cloud_paths_e2e::test_classify_candidates_legacy_yaml_path_is_intercepted",
    ],
    Feature.UNMATCHED_SUGGESTION: [
        "tests.privacy.test_web_cloud_paths::test_legacy_unmatched_suggest_is_intercepted",
    ],
    Feature.ASK_AI: ["tests.privacy.test_web_cloud_paths::test_v1_ask_ai_is_intercepted"],
    Feature.RULE_LEARNING: [
        "tests.privacy.test_cloud_paths_e2e::test_record_correction_rule_learning_is_intercepted",
    ],
    Feature.CONNECTION_TEST: [
        "tests.privacy.test_web_cloud_paths::test_legacy_test_connection_is_intercepted",
        "tests.privacy.test_web_cloud_paths::test_v1_connection_test_is_intercepted",
    ],
}


def _production_modules() -> dict[str, ast.Module]:
    return {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text(), filename=str(path))
        for path in sorted((ROOT / "docflow").rglob("*.py"))
    }


def provider_imports(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        elif (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
              and getattr(node.func, "id", getattr(node.func, "attr", "")) in
              ("__import__", "import_module")):
            names = [a.value for a in node.args if isinstance(a, ast.Constant)
                     and isinstance(a.value, str)]
        for name in names:
            found.update(m for m in PROVIDER_MODULES if name == m or name.startswith(m + "."))
    return found


def provider_calls(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in PROVIDER_CALLS[:2]:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in PROVIDER_CALLS:
            found.add(node.attr)
    return found


def gateway_call_sites(path: str, tree: ast.AST) -> set[tuple[str, str, str]]:
    sites: set[tuple[str, str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = ["<module>"]

        def visit_FunctionDef(self, node) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            for candidate in (node.func, *node.args):  # direct or asyncio.to_thread(g.method)
                if isinstance(candidate, ast.Attribute) and candidate.attr in GATEWAY_METHODS:
                    sites.add((path, self.stack[-1], candidate.attr))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


def feature_references(tree: ast.AST) -> set[Feature]:
    return {Feature[node.attr] for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "Feature" and node.attr in Feature.__members__}


def test_scanners_detect_violations() -> None:
    bad = ast.parse("import openai\nfrom httpx import Client\nimportlib.import_module('requests')\n"
                    "OpenAI().chat.completions.create()\nasync def f(g):\n"
                    "    await asyncio.to_thread(g.cluster, [])\n")
    assert provider_imports(bad) == {"openai", "httpx", "requests"}
    assert {"OpenAI", "completions"} <= provider_calls(bad)
    assert gateway_call_sites("x.py", bad) == {("x.py", "f", "cluster")}


def test_only_the_gateway_adapter_imports_or_calls_provider_sdks() -> None:
    modules = _production_modules()
    offenders = {
        (path, name)
        for path, tree in modules.items() if path != ADAPTER
        for name in provider_imports(tree) | provider_calls(tree)
    }
    assert offenders == set()
    assert "openai" in provider_imports(modules[ADAPTER])


def test_every_gateway_call_site_is_registered() -> None:
    found = set()
    for path, tree in _production_modules().items():
        if path != GATEWAY:
            found |= gateway_call_sites(path, tree)
    assert found == CALL_SITES
    public = {name for name, value in vars(CloudPromptGateway).items()
              if callable(value) and not name.startswith("_") and name != "confine"}
    assert public == GATEWAY_METHODS


def test_every_feature_is_used_prompted_and_covered_end_to_end() -> None:
    used = {IMPLIED_FEATURES[method] for _, _, method in CALL_SITES if method in IMPLIED_FEATURES}
    used.add(Feature.CLASSIFICATION)  # classify() default feature
    for path, tree in _production_modules().items():
        if path != GATEWAY:
            used |= feature_references(tree)
    assert used == set(Feature) == set(SYSTEM_PROMPTS) == set(E2E_TESTS)
    for node_ids in E2E_TESTS.values():
        for node_id in node_ids:
            module, name = node_id.split("::")
            assert callable(getattr(importlib.import_module(module), name))


def test_every_web_route_reaching_the_gateway_is_registered() -> None:
    from docflow.web.app import app

    markers = {"CloudPromptGateway", "_v1_gateway", "_suggest_filing"} | GATEWAY_METHODS
    cloud_routes = set()
    for application in (app,):
        for route in application.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None or not inspect.isfunction(endpoint):
                continue
            tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
            names = {getattr(n, "id", getattr(n, "attr", None)) for n in ast.walk(tree)}
            if names & markers:
                cloud_routes.add(route.path)
    assert cloud_routes == set(CLOUD_ROUTES)


def test_no_raw_prompt_builders_remain_outside_the_gateway() -> None:
    """Legacy builders embedded raw OCR text, rules and entities in prompt strings."""
    assert importlib.util.find_spec("docflow.llm.prompts") is None
    for path, tree in _production_modules().items():
        if path == GATEWAY:
            continue
        names = {getattr(n, "name", None) for n in ast.walk(tree)}
        assert not {n for n in names if n and n.startswith("build_") and n.endswith("_prompt")}
