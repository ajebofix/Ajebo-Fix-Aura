import os


def scan_for_null_bytes(root_dir):
    for root, dirs, files in os.walk(root_dir):
        if "$Recycle.Bin" in root or ".venv" in root:
            continue
        
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "rb") as f:
                    content = f.read()
                    if b"\x00" in content:
                        print("NULL BYTE FOUND:", path)


scan_for_null_bytes(".")
print("Scan complete.")
