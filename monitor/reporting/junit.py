"""JUnit XML, so CI systems show each check target as a test case."""

from __future__ import annotations

from collections import defaultdict
from xml.etree import ElementTree as ET

from monitor.models import RunReport, Status


def render_junit(report: RunReport) -> str:
    by_check = defaultdict(list)
    for result in report.results:
        by_check[result.check].append(result)
    root = ET.Element("testsuites", name=str(report.site.get("name")), time=f"{report.duration_ms / 1000:.3f}")
    for check, results in by_check.items():
        suite = ET.SubElement(root, "testsuite", name=check, tests=str(len(results)),
                              failures=str(sum(r.status == Status.FAIL for r in results)),
                              errors=str(sum(r.status == Status.ERROR for r in results)),
                              skipped=str(sum(r.status == Status.SKIP for r in results)))
        for r in results:
            case = ET.SubElement(suite, "testcase", classname=f"monitor.{check}", name=r.target,
                                 time=f"{(r.duration_ms or 0) / 1000:.3f}")
            body = "\n".join(r.details)
            if r.status == Status.FAIL:
                ET.SubElement(case, "failure", message=r.message).text = body
            elif r.status == Status.ERROR:
                ET.SubElement(case, "error", message=r.message).text = body
            elif r.status == Status.SKIP:
                ET.SubElement(case, "skipped", message=r.message)
            elif r.status == Status.WARNING:
                ET.SubElement(case, "system-out").text = f"WARNING: {r.message}\n{body}"
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
