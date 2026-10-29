"""Optional evidence binding for saved AI paths, without rewriting their semantics."""
import copy
import re

from . import boundary_dataset as boundary, path_first_dataset as paths


def bind_provided_process_evidence(article, catalog, raw):
    """Complete an already cited owner's input/output proof from the frozen packet.

    This is a separately recorded backend binding, not an AI-selected citation.
    It cannot add a material, choose an owner, repair foreign IDs or change a
    family, endpoint, condition or reason. The original path validator still
    decides whether the result is supported.
    """
    context = {q['id']: q for q in boundary.check_context(
        article.get('industry_context', []), catalog, article['date'])}
    value = copy.deepcopy(raw)
    changes = []
    if not isinstance(value, dict) or not isinstance(value.get('intermediates'), list):
        return value, changes
    for index, node in enumerate(value['intermediates']):
        if (not isinstance(node, dict) or node.get('edge_kind') != 'INPUT_OUTPUT'
                or not isinstance(node.get('bridge_ids'), list)
                or not all(isinstance(i, str) and i in context for i in node['bridge_ids'])):
            continue
        left, right = node.get('input_product'), node.get('product')
        if not isinstance(left, str) or not left.strip() or not isinstance(right, str) or not right.strip():
            continue
        original = list(node['bridge_ids'])
        added = set()
        # The AI must already have named this owner in its path evidence.
        for owner in {context[i]['company_code'] for i in original}:
            rows = [q for q in context.values() if q['company_code'] == owner]
            inputs = [q for q in rows if (
                q['field'] == 'upstream' or q['field'] == 'industry_background'
                and re.search(r'原料|生产成本|投入', q['text']))
                and paths.clean(left) in paths.clean(q['text'])]
            products = [q for q in rows if q['field'] == 'products'
                        and paths.clean(right) in paths.clean(q['text'])]
            if inputs and products:
                added.update(q['id'] for q in inputs + products if q['id'] not in original)
        if added:
            node['bridge_ids'] += sorted(added)
            changes.append({'node_index': index, 'AI_selected_bridge_ids': original,
                            'backend_bound_bridge_ids': sorted(added),
                            'binding': 'same_already_cited_owner; frozen input/output evidence'})
    return value, changes


def validate_saved_path(article, catalog, raw):
    """Return a checked path and explicit citation delta; never replace failures by NONE."""
    try:
        return paths.derive_pair(article, catalog, raw), []
    except ValueError as error:
        if str(error) != 'No evidence of the stated adjacent business link':
            raise
        value, changes = bind_provided_process_evidence(article, catalog, raw)
        if not changes:
            raise
        return paths.derive_pair(article, catalog, value), changes
