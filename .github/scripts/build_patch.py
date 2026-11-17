"""Recompile the single changed class against Minecraft's bundled libraries and REI."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[2]
info = json.loads((root / "mosslight-build.json").read_text())
class_path = "vectorwing/farmersdelight/integration/rei/display/CookingPotDisplay"
cache = root / "build/inputs"
cache.mkdir(parents=True, exist_ok=True)
for name, pin in info["inputs"].items():
    path = cache / (name + ".jar")
    if not path.exists():
        urllib.request.urlretrieve(pin["url"], path)
    if hashlib.new(pin["hash-format"], path.read_bytes()).hexdigest() != pin["hash"]:
        raise ValueError("Unexpected digest for " + name)

with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    classpath = [cache / "farmersdelight.jar", cache / "rei.jar", cache / "architectury.jar"]
    with zipfile.ZipFile(cache / "server.jar") as bundle:
        for member in bundle.namelist():
            if member.endswith(".jar") and member.startswith(("META-INF/versions/", "META-INF/libraries/")):
                classpath.append(Path(bundle.extract(member, work)))
    subprocess.run(["javac", "--release", "25", "-proc:none", "-classpath",
                    os.pathsep.join(map(str, classpath)), "-d", str(work),
                    str(root / "src/main/java" / (class_path + ".java"))], check=True)
    output = root / "build/libs" / info["artifact"]
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(classpath[0]) as official, zipfile.ZipFile(output, "w") as jar:
        for entry in official.infolist():
            data = official.read(entry)
            if entry.filename == class_path + ".class":
                data = (work / entry.filename).read_bytes()
            elif entry.filename == "fabric.mod.json":
                metadata = json.loads(data)
                metadata["version"] = info["version"]
                data = (json.dumps(metadata, indent=2) + "\n").encode()
            jar.writestr(entry, data)
        jar.writestr(zipfile.ZipInfo("MOSSLIGHT-PATCHES.txt"), (root / "MOSSLIGHT-PATCHES.txt").read_bytes())
