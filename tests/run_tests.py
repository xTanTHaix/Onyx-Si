"""
OnyxSi Pure Stdlib Test Runner (Direct Recipe Runner).
Discovers and executes all test functions and test classes under tests/
with zero external dependencies. Compatible with pytest test suite.
"""
import os
import sys
import time
import inspect
import importlib.util
import traceback
import types
from pathlib import Path

# The runner prints emoji markers. A legacy console code page (cp874 on a
# Thai-locale Windows, cp1252 elsewhere) cannot encode them and would abort
# the whole run with a UnicodeEncodeError, so pin the streams to UTF-8 first.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Add src and project root to sys.path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

# Custom finder to bypass 9p filesystem readdir caching
class _TestsPackageFinder:
    def find_spec(self, fullname, path, target=None):
        if not (fullname == "tests" or fullname.startswith("tests.")):
            return None
        parts = fullname.split(".")
        subpath = os.path.join(str(BASE_DIR), *parts)
        init_py = os.path.join(subpath, "__init__.py")
        if os.path.exists(init_py):
            return importlib.util.spec_from_file_location(fullname, init_py, submodule_search_locations=[subpath])
        py_file = subpath + ".py"
        if os.path.exists(py_file):
            return importlib.util.spec_from_file_location(fullname, py_file)
        return None

if not any(isinstance(f, _TestsPackageFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _TestsPackageFinder())

# Provide lightweight stdlib fallback for pytest if running in pure headless python environment
if "pytest" not in sys.modules:
    pytest_mod = types.ModuleType("pytest")
    class ExceptionInfo:
        def __init__(self):
            self.value = None
    class MockPytest:
        @staticmethod
        def fail(msg):
            raise AssertionError(msg)
        @staticmethod
        def skip(msg):
            pass
        @staticmethod
        def fixture(func):
            return func
        @staticmethod
        def raises(expected_exception):
            from contextlib import contextmanager
            info = ExceptionInfo()
            @contextmanager
            def cm():
                try:
                    yield info
                except expected_exception as e:
                    info.value = e
                else:
                    raise AssertionError(f"Expected exception {expected_exception} not raised")
            return cm()
    mock_instance = MockPytest()
    for attr in ["fail", "skip", "fixture", "raises"]:
        setattr(pytest_mod, attr, getattr(mock_instance, attr))
    sys.modules["pytest"] = pytest_mod

def load_module_from_file(file_path: Path):
    mod_name = file_path.stem
    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

def discover_and_run():
    test_manifest = [
        ("tests/normal", [
            "test_phase0_scaffolding.py",
            "test_phase0_superblock.py",
            "test_phase0_ring_buffer.py",
            "test_phase0_trie_index.py",
            "test_phase1_policy_gate.py",
            "test_phase1_sandbox_enclave.py",
            "test_phase1_syscall_guard.py",
            "test_phase1_process_manager.py",
            "test_phase1_modules.py",
            "test_p2_1_bitplane_ast.py",
            "test_p2_2_piece_table.py",
            "test_p2_3_vfs_governor.py",
            "test_p2_4_virtual_handles.py",
            "test_p3_1_ghost_overlay.py",
            "test_p3_2_cow_workspace.py",
            "test_p3_3_ast_lease.py",
            "test_p3_4_ast_merger.py",
            "test_p4_1_meta_router.py",
            "test_p4_2_grammar_shield.py",
            "test_p4_3_grammar_pipeline.py",
            "test_p4_4_stream_folder.py",
            "test_p5_1_trajectory.py",
            "test_p5_2_error_budget_wal.py",
            "test_p5_3_hypothesis_engine.py",
            "test_p5_4_bisect_antibody.py",
            "test_p6_1_task_wiki.py",
            "test_p6_2_csr_bm25.py",
            "test_p6_3_blast_test_runner.py",
            "test_p6_4_compute_alu.py",
            "test_p7_1_headless_daemon.py",
            "test_p7_2_quality_gates.py",
            "test_p8_mcp_protocol.py",
        ]),
        ("tests/street", [
            "test_phase0_street.py",
            "test_phase1_street.py",
            "test_chaos.py",
            "test_p2_street.py",
            "test_p3_street.py",
            "test_p4_street.py",
            "test_p5_street.py",
            "test_p6_street.py",
            "test_p7_street.py",
        ]),
        ("tests/character", [
            "test_character_phase0_phase1.py",
            "test_character_scenarios.py",
            "test_character_phase2.py",
            "test_character_phase3.py",
            "test_character_phase4.py",
            "test_character_phase5.py",
            "test_character_phase6.py",
            "test_character_phase7.py",
        ]),
        ("tests", [
            "test_killer_features.py",
        ]),
    ]
    
    total_tests = 0
    passed_tests = 0
    failed_tests = 0
    results = []
    
    start_time = time.perf_counter()
    print("=" * 75)
    print(" OnyxSi v1.0 Unified: Complete Direct Test Runner (Phases 0 - 7 Complete)")
    print("=" * 75)
    
    for rel_dir, test_files in test_manifest:
        tdir = BASE_DIR / rel_dir
        if not tdir.exists():
            continue
        print(f"\n📂 Discovering tests in {rel_dir}...")
        
        for fname in test_files:
            py_file = tdir / fname
            if not py_file.exists():
                continue
            rel_file = py_file.relative_to(BASE_DIR)
            
            try:
                mod = load_module_from_file(py_file)
            except Exception as e:
                print(f"  ❌ Failed to import {rel_file}: {e}")
                traceback.print_exc()
                failed_tests += 1
                total_tests += 1
                continue
            
            test_funcs = [
                (name, func) for name, func in inspect.getmembers(mod, inspect.isfunction)
                if name.startswith("test_") and func.__module__ == mod.__name__
            ]
            
            test_classes = [
                (name, cls) for name, cls in inspect.getmembers(mod, inspect.isclass)
                if (name.startswith("Test") or name.startswith("CT")) and cls.__module__ == mod.__name__
            ]
            
            for fname_func, func in test_funcs:
                total_tests += 1
                t0 = time.perf_counter()
                try:
                    func()
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    passed_tests += 1
                    print(f"  ✅ PASS: {rel_file}::{fname_func} ({dur_ms:.2f}ms)")
                    results.append((f"{rel_file}::{fname_func}", True, dur_ms, None))
                except Exception:
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    failed_tests += 1
                    err_msg = traceback.format_exc()
                    print(f"  ❌ FAIL: {rel_file}::{fname_func} ({dur_ms:.2f}ms)")
                    print(err_msg)
                    results.append((f"{rel_file}::{fname_func}", False, dur_ms, err_msg))
                    
            for cname, cls in test_classes:
                try:
                    instance = cls()
                except Exception as e:
                    print(f"  ❌ Failed to instantiate {cname}: {e}")
                    failed_tests += 1
                    total_tests += 1
                    continue

                methods = [
                    (mname, method) for mname, method in inspect.getmembers(instance, inspect.ismethod)
                    if mname.startswith("test_")
                ]
                for mname, method in methods:
                    total_tests += 1
                    t0 = time.perf_counter()
                    try:
                        method()
                        dur_ms = (time.perf_counter() - t0) * 1000.0
                        passed_tests += 1
                        print(f"  ✅ PASS: {rel_file}::{cname}::{mname} ({dur_ms:.2f}ms)")
                        results.append((f"{rel_file}::{cname}::{mname}", True, dur_ms, None))
                    except Exception:
                        dur_ms = (time.perf_counter() - t0) * 1000.0
                        failed_tests += 1
                        err_msg = traceback.format_exc()
                        print(f"  ❌ FAIL: {rel_file}::{cname}::{mname} ({dur_ms:.2f}ms)")
                        print(err_msg)
                        results.append((f"{rel_file}::{cname}::{mname}", False, dur_ms, err_msg))

    total_time = (time.perf_counter() - start_time) * 1000.0
    print("\n" + "=" * 75)
    print(f" Test Summary: {passed_tests}/{total_tests} PASSED in {total_time:.2f}ms ({failed_tests} FAILED)")
    print("=" * 75)
    
    if failed_tests > 0:
        sys.exit(1)

if __name__ == "__main__":
    discover_and_run()
