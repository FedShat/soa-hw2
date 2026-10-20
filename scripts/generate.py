"""Generate Pydantic DTOs from the checked-in OpenAPI contract."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "openapi/marketplace.yaml"
OUTPUT = ROOT / "app/generated"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "__init__.py").write_text("")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(SPEC),
            "--input-file-type",
            "openapi",
            "--output",
            str(OUTPUT / "models.py"),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--base-class",
            "app.model_base.APIModel",
            "--target-python-version",
            "3.13",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-subclass-enum",
            "--field-constraints",
            "--strict-types",
            "str",
            "int",
            "bool",
            "--field-extra-keys",
            "x-unique-by",
            "x-after",
            "--use-decimal-for-multiple-of",
            "--output-datetime-class",
            "AwareDatetime",
            "--strict-nullable",
            "--set-default-enum-member",
            "--disable-timestamp",
            "--formatters",
            "black",
            "isort",
        ],
        check=True,
        cwd=ROOT,
    )


if __name__ == "__main__":
    main()
