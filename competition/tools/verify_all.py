"""Verify every delivered branch and pull request against the live GitHub state."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gh

UPSTREAM = "oceanbase/seekdb"
FORK = "liyunlong136/seekdb"
TARGET = "tangtang-cx/seekdb"

BRANCHES = [
    ("feat/basic-practice", "基础实践题（部署 / Fork / PR）"),
    ("fix/ddl-identifier-escaping", "Bug 修复：#1469 DDL 标识符转义下沉到 oblib"),
    ("perf/filter-equivalence-selectivity", "性能优化：等价过滤条件统一选择率"),
]

PR_NUMBERS = [2586, 2587, 2588]

print("=" * 78)
print("1. 分支在两个远端的落地情况")
print("=" * 78)
for branch, desc in BRANCHES:
    shas = {}
    for repo in (TARGET, FORK):
        st, data = gh.get("/repos/%s/branches/%s" % (repo, branch))
        shas[repo] = data.get("commit", {}).get("sha", "") if st == 200 else "MISSING"
    same = len(set(shas.values())) == 1 and "MISSING" not in shas.values()
    print("\n%-34s %s" % (branch, desc))
    for repo in (TARGET, FORK):
        print("   %-22s %s" % (repo, shas[repo]))
    print("   两端一致: %s" % ("是" if same else "否"))

print()
print("=" * 78)
print("2. 向上游提交的 PR")
print("=" * 78)
for num in PR_NUMBERS:
    st, pr = gh.get("/repos/%s/pulls/%d" % (UPSTREAM, num))
    if st != 200:
        print("PR #%d 读取失败: %s" % (num, pr))
        continue
    print()
    print("PR #%d  %s" % (pr["number"], pr["title"]))
    print("   url      : %s" % pr["html_url"])
    print("   state    : %s (merged=%s)" % (pr["state"], pr.get("merged")))
    print("   base     : %s @ %s" % (pr["base"]["ref"], pr["base"]["sha"][:12]))
    print("   head     : %s @ %s" % (pr["head"]["label"], pr["head"]["sha"][:12]))
    print("   author   : %s" % pr["user"]["login"])
    print("   files    : %s (+%s/-%s)" % (pr.get("changed_files"), pr.get("additions"), pr.get("deletions")))

print()
print("=" * 78)
print("3. 目标仓库 tangtang-cx/seekdb 可见性")
print("=" * 78)
st, repo = gh.get("/repos/%s" % TARGET)
print("   visibility : %s" % repo.get("visibility"))
print("   default    : %s" % repo.get("default_branch"))

st, fork = gh.get("/repos/%s" % FORK)
print("   %s visibility=%s fork=%s parent=%s" % (
    FORK, fork.get("visibility"), fork.get("fork"), (fork.get("parent") or {}).get("full_name")))

print()
print("=" * 78)
print("4. 遥测 ID（本机实测）")
print("=" * 78)
telemetry = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "seekdb_instance", "run", "telemetry.json")
if os.path.isfile(telemetry):
    import json
    with open(telemetry, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print("   content.id : %s" % data["content"]["id"])
    print("   version    : %s" % data["content"]["version"])
    print("   event      : %s" % data["content"]["event"])
else:
    print("   (遥测文件未找到)")
