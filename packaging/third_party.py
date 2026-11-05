import re
from importlib import metadata
from pathlib import Path

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
LICENSE_NAMES = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.IGNORECASE)
HERSHEY = """The Hershey Fonts were originally created by Dr. A. V. Hershey while working at the U. S. National Bureau of Standards.

The format of the font data in this distribution was originally created by James Hurt, Cognition, Inc., 900 Technology Park Drive, Billerica, MA 01821."""


def python_closure():
    env = default_environment()
    seen, queue = {}, [("kerfline", ())]
    while queue:
        name, extras = queue.pop()
        key = name.lower().replace("_", "-")
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        if key in seen and set(extras) <= seen[key][1]:
            continue
        seen[key] = (dist, set(extras) | (seen[key][1] if key in seen else set()))
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker and not any(
                requirement.marker.evaluate({**env, "extra": extra}) for extra in (list(extras) or [""])
            ):
                continue
            queue.append((requirement.name, tuple(requirement.extras)))
    return [dist for key, (dist, _) in sorted(seen.items()) if key != "kerfline"]


def license_text(dist):
    texts = []
    for item in dist.files or []:
        if LICENSE_NAMES.search(str(item)) and ".dist-info" in str(item):
            try:
                texts.append(Path(dist.locate_file(item)).read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                continue
    return "\n\n".join(text for text in texts if text)


def main():
    parts = [
        "# Third-party notices",
        "",
        "Kerfline's apps include the components below. Each keeps its own license, reproduced here as those licenses require. Kerfline's own license is in LICENSE.",
        "",
        "## Hershey Fonts data",
        "",
        HERSHEY,
        "",
        "## Python packages",
        "",
    ]
    for dist in python_closure():
        meta = dist.metadata
        classifiers = meta.get_all("Classifier") or []
        name = meta.get("License-Expression") or meta.get("License") or ", ".join(
            item.split(" :: ")[-1] for item in classifiers if item.startswith("License ::")
        )
        name = name.splitlines()[0][:80] if name else "see text"
        parts += [f"### {meta['Name']} {dist.version}", "", f"License: {name}", ""]
        parts += ["```", license_text(dist) or "License file not shipped with the package. See the project's homepage.", "```", ""]
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(parts), encoding="utf-8")
    print("wrote THIRD_PARTY_NOTICES.md")


if __name__ == "__main__":
    main()
