"""Explicit, source-reviewed schemas for joining logical financial rows.

No cell alignment is guessed. Callers archive originals and declare the schema;
incomplete metrics/units remain errors, including at an excerpt boundary.
"""
import re

from .sources import financial_table_reasons


def reconstruct(tables, *, schema, units, limit=5):
    if schema not in {"four", "spanned-eight"} or not isinstance(limit, int) or limit < 1:
        raise ValueError("Unknown financial schema or invalid row limit")
    width = 4 if schema == "four" else 8
    positions = (0, 1, 2, 3) if width == 4 else (1, 3, 4, 5)
    compact = lambda cell: re.sub(r"\s+", "", cell or "")
    header, logical = None, []
    for table in tables:
        for physical in table:
            if len(physical) != width or any(c is not None and not isinstance(c, str) for c in physical):
                raise ValueError("Unknown financial columns")
            cells = [compact(c) for c in physical]
            if header is None:
                if schema == "four":
                    if cells[0] not in {"项目", "指标"} or not cells[1].startswith("本报告期"):
                        raise ValueError("Unknown financial header")
                    header = cells
                else:
                    if cells != ["", "", "", "本报告期", "上年同期", "", "本报告期比上年同期增减", ""]:
                        raise ValueError("Unknown spanned header")
                    header = ["项目", cells[3], cells[4], cells[6]]
                continue
            if schema == "spanned-eight" and cells == ["", "", "", "", "", "", "（%）", ""]:
                header[-1] += "（%）"; continue
            if (len(logical) >= limit and cells[positions[1]] == "本报告期末"
                    and cells[positions[2]] == "上年度末"):
                break
            if any(c for i, c in enumerate(cells) if i not in positions):
                raise ValueError("Nonempty unmapped financial cell")
            row = [cells[i] for i in positions]
            if not any(row): continue
            if row[0] and not any(row[1:]):
                if not logical: raise ValueError("Orphan financial continuation")
                logical[-1][0] += row[0]
            elif all(row):
                logical.append(row)
            else:
                # A later balance-sheet header is outside the declared metric section.
                if not row[0] and row[1].startswith("本报告期末") and len(logical) >= limit:
                    break
                raise ValueError("Unresolved partial financial row")
        if len(logical) > limit: break
    if not header or len(logical) < limit:
        raise ValueError("Requested complete metrics not present")
    result = [header] + logical[:limit]
    reasons = financial_table_reasons(result, units)
    if reasons: raise ValueError("; ".join(reasons))
    return result
