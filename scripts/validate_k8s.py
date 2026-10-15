#!/usr/bin/env python3
"""
Kubernetes Manifest Validator (Offline / Pre-flight CI/CD Check)
Validates all Kubernetes YAML resources for syntax, structure, apiVersion, kind, and metadata.
"""

import sys
import glob
from pathlib import Path
import yaml

# Ensure UTF-8 output even on legacy Windows code pages
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def validate_manifests(directory: str = "k8s") -> bool:
    manifest_files = sorted(glob.glob(f"{directory}/*.yaml") + glob.glob(f"{directory}/*.yml"))
    if not manifest_files:
        print(f"[ERROR] No Kubernetes manifests found in '{directory}'")
        return False

    print(f"[INFO] Validating {len(manifest_files)} Kubernetes manifests in '{directory}'...\n")
    all_valid = True
    total_resources = 0

    for file_path in manifest_files:
        path = Path(file_path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
            
            non_empty_docs = [d for d in docs if d is not None]
            if not non_empty_docs:
                print(f"[WARN] {path.name}: Empty document")
                continue

            for idx, doc in enumerate(non_empty_docs):
                total_resources += 1
                api_version = doc.get("apiVersion")
                kind = doc.get("kind")
                metadata = doc.get("metadata", {})
                name = metadata.get("name", "<unnamed>")

                if not api_version:
                    print(f"[ERROR] {path.name} (doc #{idx+1}): Missing 'apiVersion'")
                    all_valid = False
                elif not kind:
                    print(f"[ERROR] {path.name} (doc #{idx+1}): Missing 'kind'")
                    all_valid = False
                elif not metadata or not name:
                    print(f"[ERROR] {path.name} (doc #{idx+1}): Missing 'metadata.name'")
                    all_valid = False
                else:
                    print(f"   [OK] {path.name:<28} -> {kind:<22} ({name})")

        except yaml.YAMLError as e:
            print(f"[ERROR] {path.name}: YAML parsing error: {e}")
            all_valid = False
        except Exception as e:
            print(f"[ERROR] {path.name}: Unexpected validation error: {e}")
            all_valid = False

    print(f"\n{'='*55}")
    if all_valid:
        print(f"[SUCCESS] All {total_resources} Kubernetes resources validated successfully!")
    else:
        print(f"[FAILURE] Some Kubernetes manifests failed validation.")
    print(f"{'='*55}\n")
    return all_valid

if __name__ == "__main__":
    success = validate_manifests()
    sys.exit(0 if success else 1)
