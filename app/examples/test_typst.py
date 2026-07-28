"""Test typst API: in-process compile with sys_inputs."""
import typst
import tempfile, os, json

# Use project directory to avoid temp path issues
test_dir = os.path.dirname(os.path.abspath(__file__))

# Template that reads cv from sys.inputs
typ_path = os.path.join(test_dir, "_test_doc.typ")
with open(typ_path, "w") as f:
    f.write("#let data = json(sys.inputs.at(\"cv\"))\n")
    f.write("Name: #data.name\n")
    f.write("Skills: #data.skills.join(\", \")\n")

out_path = os.path.join(test_dir, "_test_out.pdf")
cv_data = {"name": "Alice", "skills": ["Python", "React"]}
try:
    typst.compile(typ_path, output=out_path,
                  sys_inputs={"cv": json.dumps(cv_data)})
    sz = os.path.getsize(out_path)
    print(f"sys_inputs compile: size={sz}")
except Exception as e:
    print(f"sys_inputs error: {type(e).__name__}: {e}")

# Include test
inc_dir = os.path.join(test_dir, "_test_lib")
os.makedirs(inc_dir, exist_ok=True)
with open(os.path.join(inc_dir, "helper.typ"), "w") as f:
    f.write("#let section(title, body) = {\n")
    f.write("  [#title]\n")
    f.write("  #body\n")
    f.write("}\n")
main_path = os.path.join(test_dir, "_test_main.typ")
with open(main_path, "w") as f:
    f.write('#include "_test_lib/helper.typ"\n')
    f.write('#section("Test", [Hello from include])\n')
out2 = os.path.join(test_dir, "_test_out2.pdf")
try:
    typst.compile(main_path, output=out2, root=test_dir)
    print(f"include compile: size={os.path.getsize(out2)}")
except Exception as e:
    print(f"include error: {type(e).__name__}: {e}")

# Cleanup
for f in [typ_path, out_path, main_path, out2]:
    try: os.remove(f)
    except: pass
try: os.rmdir(inc_dir)
except: pass

print("Done")
