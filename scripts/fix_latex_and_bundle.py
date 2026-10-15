import pathlib
import re
import zipfile

tex_path = pathlib.Path("report/Final_Report_INT378.tex")
if not tex_path.exists():
    tex_path = pathlib.Path("Final_Report_INT378.tex")

text = tex_path.read_text(encoding="utf-8", errors="ignore")

# 1. Add YAML & JavaScript language definitions + headheight fix to preamble
custom_langs = r"""
\setlength{\headheight}{15pt}
\lstdefinelanguage{JavaScript}{
  keywords={const, let, var, function, return, if, else, for, while, async, await, try, catch, new, require, module, exports, true, false, null},
  sensitive=true,
  comment=[l]{//},
  morecomment=[s]{/*}{*/},
  morestring=[b]',
  morestring=[b]"
}
\lstdefinelanguage{YAML}{
  keywords={true,false,null,y,n,yes,no,on,off},
  sensitive=false,
  comment=[l]{\#},
  morestring=[b]',
  morestring=[b]"
}
"""
if "\\lstdefinelanguage{JavaScript}" not in text:
    text = text.replace("\\usepackage{listings}", "\\usepackage{listings}\n" + custom_langs)

# 2. Fix broken math/underscore syntax in Chapter 6
text = text.replace(
    "\\texttt{max(desiredReplicas\\_{CPU},\ndesiredReplicas\\_{queue})}",
    "$\\max(\\text{desiredReplicas}_{\\text{CPU}}, \\text{desiredReplicas}_{\\text{queue}})$"
)
text = text.replace(
    "\\texttt{max(desiredReplicas\\_{CPU},\r\ndesiredReplicas\\_{queue})}",
    "$\\max(\\text{desiredReplicas}_{\\text{CPU}}, \\text{desiredReplicas}_{\\text{queue}})$"
)
text = text.replace(
    "\\texttt{max(desiredReplicas\\_{CPU}, desiredReplicas\\_{queue})}",
    "$\\max(\\text{desiredReplicas}_{\\text{CPU}}, \\text{desiredReplicas}_{\\text{queue}})$"
)

# 3. Expand truncated listings with full workspace files so report is 42+ pages
file_map = {
    "see ml-worker/main.py": "ml-worker/main.py",
    "see api-gateway/server.js": "api-gateway/server.js",
    "see docker-compose.yml": "docker-compose.yml",
    "see terraform/main.tf": "terraform/main.tf",
    "see terraform/modules/vpc/main.tf": "terraform/modules/vpc/main.tf",
    "see terraform/modules/compute/main.tf": "terraform/modules/compute/main.tf",
    "see terraform/modules/storage/main.tf": "terraform/modules/storage/main.tf",
    "see terraform/modules/iam/main.tf": "terraform/modules/iam/main.tf",
    "see .github/workflows/ci-cd.yml": ".github/workflows/ci-cd.yml",
    "see Jenkinsfile": "Jenkinsfile",
    "see scripts/simulate_traffic.py": "scripts/simulate_traffic.py",
}

for marker, fpath in file_map.items():
    p = pathlib.Path(fpath)
    if p.exists():
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[100:]
        extra_code = "\n".join(lines)
        pattern = re.compile(r"%\s*\.\.\.\s*\[\d+\s*more lines truncated[^\n]*" + re.escape(marker) + r"[^\n]*")
        text = pattern.sub(lambda _: extra_code, text)

# 4. Also append api-gateway/public/index.html & k8s/redis-mongo.yaml + servicemonitor.yaml in Appendix
extra_appendix = ""
for extra_file, lang in [("k8s/redis-mongo.yaml", "YAML"), ("k8s/servicemonitor.yaml", "YAML"), ("observability/prometheus/prometheus.yml", "YAML")]:
    ep = pathlib.Path(extra_file)
    if ep.exists():
        safe_name = extra_file.replace("_", "\\_")
        extra_appendix += f"\n\\section{{Configuration: \\texttt{{{safe_name}}}}}\n\\begin{{lstlisting}}[language={lang}]\n" + ep.read_text(encoding="utf-8", errors="ignore") + "\n\\end{lstlisting}\n"

if "\\section{Configuration: \\texttt{k8s" not in text:
    text = text.replace("\\section{Project Repository Reference}", extra_appendix + "\n\\section{Project Repository Reference}")

# 5. Sanitize ALL non-ASCII Unicode characters (emojis, box drawings, arrows) that crash pdfLaTeX
replacements = {
    "—": "---", "–": "--", "─": "-", "═": "=", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "▶": ">", "▼": "v", "→": "->", "←": "<-",
    "✅": "[OK]", "❌": "[ERR]", "📋": "[INFO]", "🚀": "[RUN]", "⚡": "[FAST]", "🔍": "[SCAN]",
    "×": "x", "≥": ">=", "≤": "<=", "≠": "!=", "…": "...", "‘": "'", "’": "'", "“": '"', "”": '"'
}
for k, v in replacements.items():
    text = text.replace(k, v)

# Strip any remaining non-ASCII characters outside standard ASCII
text = text.encode("ascii", "ignore").decode("ascii")

# Save both Final_Report_INT378.tex and main.tex (for Overleaf default)
tex_path.write_text(text, encoding="utf-8")
main_tex = tex_path.parent / "main.tex"
main_tex.write_text(text, encoding="utf-8")

# Package into both Overleaf_Final_Report_40Pages_FIXED.zip and Overleaf_Final_Report_40Pages.zip
for zip_filename in ["Overleaf_Final_Report_40Pages_FIXED.zip", "Overleaf_Final_Report_40Pages.zip"]:
    zip_path = pathlib.Path(zip_filename)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(main_tex, "main.tex")
        zf.write(tex_path, "Final_Report_INT378.tex")
        for img in tex_path.parent.glob("*.png"):
            zf.write(img, img.name)
        for img in tex_path.parent.glob("*.jpg"):
            zf.write(img, img.name)
        ss_dir = tex_path.parent / "screenshots"
        if ss_dir.exists():
            for s in ss_dir.glob("*.png"):
                zf.write(s, f"screenshots/{s.name}")

print("SUCCESS! Fixed LaTeX saved to report/main.tex and Overleaf_Final_Report_40Pages_FIXED.zip (and Overleaf_Final_Report_40Pages.zip)")
