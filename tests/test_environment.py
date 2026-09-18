"""
Basic Environment and Dependency Verification Test.
Verifies that essential foundational libraries can be imported and prints their versions.
"""

import sys

def test_imports():
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version}\n")
    
    modules = [
        ("numpy", "NumPy"),
        ("pandas", "Pandas"),
        ("sklearn", "Scikit-Learn"),
        ("matplotlib", "Matplotlib"),
    ]
    
    all_passed = True
    for module_name, display_name in modules:
        try:
            mod = __import__(module_name)
            version = getattr(mod, "__version__", "unknown")
            print(f"[PASS] {display_name} successfully imported: v{version}")
        except ImportError as e:
            print(f"[FAIL] Failed to import {display_name} ({module_name}): {e}")
            all_passed = False
            
    if all_passed:
        print("\nAll core foundational packages imported successfully.")
        return 0
    else:
        print("\nOne or more core packages failed to import.")
        return 1

if __name__ == "__main__":
    sys.exit(test_imports())
