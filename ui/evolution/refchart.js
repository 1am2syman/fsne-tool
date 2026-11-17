/* Reference Sankey renderer adapted from render_html() in sankey-chart/category_evolution_sankey/build_category_evolution_sankey.py
 * (sankey-chart @ a555741, builder sha256 f095bc3d0557b216). This host adaptation is maintained here.
 * FSNE host: lazy lineage details, exact tracing, and category/band context menus.
 * page.js owns cohort loading, scope and Back navigation; layout remains in this renderer.
 * Requires local D3 7.9.2 and d3-sankey 0.12.3. Host contract: FSNE.refChart.create(host, DATASETS). */
(function (root) {
  'use strict';
  const FSNE = root.FSNE || (root.FSNE = {});
  FSNE.refChart = { create: function create(host, DATASETS) {
    const controller = {current: null};
    const svg = d3.select(host.svg);
    const tooltip = d3.select(host.tooltip);
    const drilldownContextMenu = d3.select(host.menu);
    const lensOptions = DATASETS.metric_options || [];
    const defaultLens = DATASETS.default_metric || (lensOptions[0] && lensOptions[0].key);
    const RSL_GROUP_LENS_KEY = 'rsl_group_total_closing_stock_bdt';
    const EXCLUDED_RSL_BUCKET = '>10M';
    const RSL_DOWNGRADE_CATEGORY = 'transitioned_from_10m';
    const RSL_DOWNGRADE_LABEL = 'Transitioned from >10M';
    const RSL_DOWNGRADE_COLOR = '#7C3AED';
    let horizontalSpacingPx = host.spacing || 240;
    let activeLens = DATASETS.default_metric || defaultLens;
    let includeRslOver10M = Boolean(host.includeOver10);
    let DATA = displayDataForLens(activeLens);
    let nodeMetaById = new Map(DATA.nodes.map(d => [d.id, d]));
    /* FSNE host: a narrow filter can leave a fraction of a crore; keep nonzero amounts readable. */
    const fmtValue = value => d3.format(value > 0 && value < 0.01 ? ',.8~f' : ',.2f')(value);
    const fmtContribution = d3.format(',.1%');
    const thicknessScale = 0.25;
    const RSL_ORDER = ['>10M', '6M–10M', '3M–6M', '<3M', 'Unknown'];
    const RSL_LABELS = {'>10M':'>10M', '6M–10M':'6M–10M', '3M–6M':'3M–6M', '<3M':'<3M', Unknown:'Unknown'};
    const RSL_COLORS = {'<3M':'#DC2626', '3M–6M':'#F59E0B', '6M–10M':'#2563EB', '>10M':'#059669', Unknown:'#64748B', [RSL_DOWNGRADE_CATEGORY]: RSL_DOWNGRADE_COLOR};
    const labelNodeGap = 7;
    const labelLineGap = 13;
    const labelBlockHeight = labelLineGap * 2;
    const labelNodeClearance = 8;
    const nodeVisualPadding = labelNodeGap + labelBlockHeight + labelNodeClearance;

    function endpointNode(endpoint) {
      if (!endpoint) return null;
      return typeof endpoint === 'object' ? endpoint : nodeMetaById.get(endpoint);
    }

    function categoryEndpointColor(endpoint) {
      const node = endpointNode(endpoint);
      return node && node.category ? node.color : null;
    }

    function endpointColor(endpoint) {
      const node = endpointNode(endpoint);
      return node ? node.color : null;
    }

    function linkColor(d) {
      return categoryEndpointColor(d.source) || categoryEndpointColor(d.target) || endpointColor(d.source) || endpointColor(d.target) || '#8da2c0';
    }

    function visualNodeY0(d) {
      const center = (d.y0 + d.y1) / 2;
      return center - ((d.y1 - d.y0) * thicknessScale / 2);
    }

    function visualNodeY1(d) {
      const center = (d.y0 + d.y1) / 2;
      return center + ((d.y1 - d.y0) * thicknessScale / 2);
    }

    function visualLinkY(node, y) {
      const center = (node.y0 + node.y1) / 2;
      return center + ((y - center) * thicknessScale);
    }

    function nodeLabelY(d) {
      return visualNodeY1(d) + labelNodeGap;
    }

    function visualLinkPath(d) {
      const x0 = d.source.x1;
      const x1 = d.target.x0;
      const y0 = visualLinkY(d.source, d.y0);
      const y1 = visualLinkY(d.target, d.y1);
      const xi = d3.interpolateNumber(x0, x1);
      const x2 = xi(0.5);
      return `M${x0},${y0}C${x2},${y0} ${x2},${y1} ${x1},${y1}`;
    }

    function repackColumnsFromTop(graph, top, visualPadding) {
      const columns = d3.group(graph.nodes, d => d.column);
      for (const nodes of columns.values()) {
        nodes.sort((a, b) => d3.ascending(a.sort, b.sort) || d3.ascending(a.label, b.label));
        let y = top;
        for (const node of nodes) {
          const fullHeight = node.y1 - node.y0;
          const visualHeight = Math.max(1, fullHeight * thicknessScale);
          node.y0 = y - ((fullHeight - visualHeight) / 2);
          node.y1 = node.y0 + fullHeight;
          y += visualHeight + visualPadding;
        }
      }
    }

    function activeLensOption() {
      return lensOptions.find(item => item.key === activeLens) || {};
    }

    function activeCategoryDimension() {
      const lens = ((DATA.metadata || {}).lens || {});
      return lens.category_dimension || activeLensOption().category_dimension || '';
    }

    function activeDrilldownScopeColumn() {
      const lens = ((DATA.metadata || {}).lens || {});
      return lens.drilldown_scope_column || activeLensOption().drilldown_scope_column || '';
    }

    function activeDrilldownScopeLabel() {
      const scopeColumn = activeDrilldownScopeColumn();
      if (scopeColumn === 'included_in_over_10m_exposure_cohort_lens') return 'Exposure Originating in >10M Cohort';
      return scopeColumn;
    }

    function isSupportedDrilldownNode(d) {
      return d && d.kind === 'category' && d.id && !String(d.id).startsWith('rsl-downgrade:');
    }

    function drilldownFilterLabel() {
      const dimension = activeCategoryDimension();
      if (dimension === 'rsl_group') return 'RSL Group';
      if (dimension === 'mitigation_category') return 'Mitigation Visual Category';
      return 'Category';
    }

    function hideDrilldownContextMenu() {
      drilldownContextMenu.classed('open', false).style('left', null).style('top', null).html('');
    }

    function placeDrilldownContextMenu(event) {
      const node = drilldownContextMenu.node();
      if (!node) return;
      const rect = node.getBoundingClientRect();
      const left = Math.min(event.clientX, window.innerWidth - rect.width - 12);
      const top = Math.min(event.clientY, window.innerHeight - rect.height - 12);
      drilldownContextMenu.style('left', `${Math.max(12, left)}px`).style('top', `${Math.max(12, top)}px`);
    }

    function showDrilldownContextMenu(event, d, reason = '') {
      event.preventDefault();
      event.stopPropagation();
      if (host.openRsl && host.canDrillRsl && (isSupportedDrilldownNode(d) || (d.source && d.target))) {
        const title = d.source ? `${d.source.label} → ${d.target.label}` : d.label;
        drilldownContextMenu.html(`<div class="drilldown-context-title">${escapeHtml(title)}</div><button type="button" role="menuitem">Show RSL evolution</button>`).classed('open', true);
        drilldownContextMenu.select('button').on('click', () => { hideDrilldownContextMenu(); host.openRsl(d); });
        placeDrilldownContextMenu(event);
        drilldownContextMenu.select('button').node().focus({preventScroll: true});
        return;
      }
      if (!host.openPivot) return;
      const supported = isSupportedDrilldownNode(d);
      const title = supported ? d.label : (d && d.label ? d.label : 'Unsupported target');
      const scopeLabel = activeDrilldownScopeLabel();
      const detail = supported
        ? `Node only · ${drilldownFilterLabel(d)} · ${d.month || d.month_key || 'selected month'}${scopeLabel ? ` · scoped to ${scopeLabel}` : ''}`
        : (reason || 'Only category nodes support drilldown in this MVP. Link, month hub, and side-branch drilldowns are deferred.');
      drilldownContextMenu
        .html(`<div class="drilldown-context-title">${escapeHtml(title)}</div><div class="drilldown-context-detail">${escapeHtml(detail)}</div><button type="button" role="menuitem" ${supported ? '' : 'disabled'}>Drill down in pivot</button>`)
        .classed('open', true);
      const button = drilldownContextMenu.select('button');
      if (supported) {
        button.on('click', (clickEvent) => {
          clickEvent.stopPropagation();
          hideDrilldownContextMenu();
          host.openPivot(d);
        });
      }
      placeDrilldownContextMenu(event);
    }

    d3.select(document).on('click.drilldownContextMenu', hideDrilldownContextMenu);
    d3.select(document).on('keydown.drilldownContextMenu', (event) => { if (event.key === 'Escape') hideDrilldownContextMenu(); });

    function activeTooltipMixMode() {
      return (((DATA.metadata || {}).lens || {}).tooltip_mix_mode) || 'rsl_mix';
    }

    function shouldShowTooltipMix() {
      return activeTooltipMixMode() !== 'none' && !isRslGroupLens();
    }

    function isRslGroupLensKey(lensKey) {
      const source = DATASETS.metrics[lensKey] || {};
      const lens = (source.metadata || {}).lens || {};
      return lensKey === RSL_GROUP_LENS_KEY || lens.category_dimension === 'rsl_group';
    }

    function isRslGroupLens() {
      return isRslGroupLensKey(activeLens);
    }

    function usesSecondarySalesExitSplit() {
      const metadata = DATA.metadata || {};
      if (metadata.uses_secondary_sales_exit_split) return true;
      return (DATA.nodes || []).some(node => ['sec_sales', 'rotated_adjusted'].includes(node.category || node.kind));
    }

    function rowKey(row, keyColumns) {
      return row.slice(0, keyColumns).map(value => String(value)).join('');
    }

    function mergeRows(target, rows, keyColumns, valueIndex) {
      const byKey = new Map((target || []).map(row => [rowKey(row, keyColumns), row.slice()]));
      (rows || []).forEach(row => {
        const key = rowKey(row, keyColumns);
        const existing = byKey.get(key) || row.slice(0, valueIndex + 1).map((value, index) => index === valueIndex ? 0 : value);
        existing[valueIndex] = Number(existing[valueIndex] || 0) + Number(row[valueIndex] || 0);
        byKey.set(key, existing);
      });
      return Array.from(byKey.values()).filter(row => Number(row[valueIndex] || 0) > 0.01);
    }

    function mergeIds(target, ids) {
      return Array.from(new Set([...(target || []), ...(ids || [])])).sort((a, b) => Number(a) - Number(b));
    }

    function mergeRecordTotals(target, record) {
      target.value = Number(target.value || 0) + Number(record.value || 0);
      target.value_cr = valueCr(target.value);
      if (record && record.id && String(record.id).startsWith('T')) target.detail_transition_ids = mergeIds(target.detail_transition_ids, [record.id]);
      target.slice_ids = mergeIds(target.slice_ids, record.slice_ids || (record.slice_values || []).map(row => row[0]));
      target.slice_values = mergeRows(target.slice_values, record.slice_values, 1, 1);
      target.lineage_values = mergeRows(target.lineage_values, record.lineage_values, 1, 1);
      target.exposure_mix_values = mergeRows(target.exposure_mix_values, record.exposure_mix_values, 1, 1);
      target.exposure_mix_lineage_values = mergeRows(target.exposure_mix_lineage_values, record.exposure_mix_lineage_values, 2, 2);
      target.brand_values = mergeRows(target.brand_values, record.brand_values, 1, 1);
      target.brand_lineage_values = mergeRows(target.brand_lineage_values, record.brand_lineage_values, 2, 2);
      target.rsl_values = mergeRows(target.rsl_values, record.rsl_values, 1, 1);
      target.rsl_lineage_values = mergeRows(target.rsl_lineage_values, record.rsl_lineage_values, 2, 2);
      target.slice_count = target.slice_ids.length;
    }

    function flowKey(source, target) {
      return `${source}|${target}`;
    }

    function isExcludedRslNode(node) {
      return node && node.kind === 'category' && node.category === EXCLUDED_RSL_BUCKET;
    }

    function isMonthNode(node) {
      return node && node.kind === 'month';
    }

    function scopedMonthLabel(node) {
      if (!isMonthNode(node)) return node.label;
      return String(node.label || '').replace(/ opening stock$/, ' opening stock (excl. >10M)');
    }

    function emptyScopedRecord(record) {
      return {
        ...record,
        value: 0,
        value_cr: 0,
        slice_ids: [],
        slice_values: [],
        lineage_values: [],
        rsl_values: [],
        rsl_lineage_values: [],
        exposure_mix_values: [],
        exposure_mix_lineage_values: [],
        brand_values: [],
        brand_lineage_values: [],
        detail_transition_ids: [],
        slice_count: 0,
        top_batches: [],
      };
    }

    function sumRecords(records) {
      return d3.sum(records || [], record => Number(record.value || 0));
    }

    function warnTransitionCoverage(source, sourceNodes, transitionsBySourceJunction, transitionsByJunctionTarget) {
      const tolerance = 0.05;
      const issues = [];
      (source.links || []).forEach(link => {
        const sourceNode = sourceNodes.get(link.source);
        const targetNode = sourceNodes.get(link.target);
        const expected = Number(link.value || 0);
        if (isMonthNode(targetNode)) {
          const records = transitionsBySourceJunction.get(flowKey(link.source, link.target)) || [];
          const actual = sumRecords(records);
          if (!records.length || Math.abs(actual - expected) > tolerance) {
            issues.push({direction: 'into_month', link_id: link.id, source: link.source, target: link.target, link_value: expected, transition_value: actual});
          }
        }
        if (isMonthNode(sourceNode)) {
          const records = transitionsByJunctionTarget.get(flowKey(link.source, link.target)) || [];
          const actual = sumRecords(records);
          if (!records.length || Math.abs(actual - expected) > tolerance) {
            issues.push({direction: 'out_of_month', link_id: link.id, source: link.source, target: link.target, link_value: expected, transition_value: actual});
          }
        }
      });
      if (issues.length) console.warn('[category-evolution-sankey] RSL transition coverage mismatch while excluding >10M', issues);
    }

    function syntheticDowngradeNodeId(sourceNode, junctionNode) {
      return `rsl-downgrade:${sourceNode.month_key}:${junctionNode.month_key}`;
    }

    function makeSyntheticDowngradeNode(sourceNode, junctionNode) {
      return {
        id: syntheticDowngradeNodeId(sourceNode, junctionNode),
        label: RSL_DOWNGRADE_LABEL,
        kind: 'rsl_downgrade',
        month_key: sourceNode.month_key,
        month: sourceNode.month,
        category: RSL_DOWNGRADE_CATEGORY,
        column: sourceNode.column,
        color: RSL_DOWNGRADE_COLOR,
        sort: 89,
        rsl_values: [],
        exposure_mix_values: [],
        brand_values: [],
      };
    }

    function makeSyntheticDowngradeLink(sourceNode, junctionNode, syntheticNode) {
      return {
        id: `DWN:${syntheticNode.id}`,
        source: syntheticNode.id,
        target: junctionNode.id,
        source_label: syntheticNode.label,
        target_label: junctionNode.label,
        value: 0,
        value_cr: 0,
        slice_ids: [],
        slice_values: [],
        lineage_values: [],
        rsl_values: [],
        rsl_lineage_values: [],
        exposure_mix_values: [],
        exposure_mix_lineage_values: [],
        brand_values: [],
        brand_lineage_values: [],
        detail_transition_ids: [],
        slice_count: 0,
        top_batches: [],
        raw_edges: [{source: sourceNode.id, target: junctionNode.id, note: RSL_DOWNGRADE_LABEL}],
      };
    }

    function scopedLinkFromTransitions(link, records, nodesById) {
      const scoped = emptyScopedRecord({...link});
      records.forEach(record => mergeRecordTotals(scoped, record));
      scoped.source_label = (nodesById.get(scoped.source) || {}).label || scoped.source_label;
      scoped.target_label = (nodesById.get(scoped.target) || {}).label || scoped.target_label;
      return scoped;
    }

    function recomputeScopedNodes(nodes, links) {
      const incoming = new Map(nodes.map(node => [node.id, emptyScopedRecord({value: 0})]));
      const outgoing = new Map(nodes.map(node => [node.id, emptyScopedRecord({value: 0})]));
      links.forEach(link => {
        if (outgoing.has(link.source)) mergeRecordTotals(outgoing.get(link.source), link);
        if (incoming.has(link.target)) mergeRecordTotals(incoming.get(link.target), link);
      });
      return nodes.map(node => {
        const inRecord = incoming.get(node.id) || emptyScopedRecord({value: 0});
        const outRecord = outgoing.get(node.id) || emptyScopedRecord({value: 0});
        const chosen = Number(inRecord.value || 0) >= Number(outRecord.value || 0) ? inRecord : outRecord;
        return {
          ...node,
          rsl_values: [],
          exposure_mix_values: chosen.exposure_mix_values || [],
          brand_values: chosen.brand_values || [],
        };
      });
    }

    function withoutDominantRslBucket(source) {
      const sourceNodes = new Map((source.nodes || []).map(node => [node.id, node]));
      const nodesById = new Map((source.nodes || [])
        .filter(node => !isExcludedRslNode(node))
        .map(node => [node.id, {...node, label: scopedMonthLabel(node)}]));
      const transitionsBySourceJunction = d3.group(source.transitions || [], transition => flowKey(transition.source, transition.junction));
      const transitionsByJunctionTarget = d3.group(source.transitions || [], transition => flowKey(transition.junction, transition.target));
      warnTransitionCoverage(source, sourceNodes, transitionsBySourceJunction, transitionsByJunctionTarget);

      const visibleLinks = [];
      (source.links || []).forEach(link => {
        const sourceNode = sourceNodes.get(link.source);
        const targetNode = sourceNodes.get(link.target);
        if (isExcludedRslNode(sourceNode) || isExcludedRslNode(targetNode)) return;
        const records = transitionsBySourceJunction.get(flowKey(link.source, link.target)) || [];
        const keptRecords = records.filter(record => !isExcludedRslNode(sourceNodes.get(record.source)) && !isExcludedRslNode(sourceNodes.get(record.target)));
        const nextLink = isMonthNode(targetNode) && records.length ? scopedLinkFromTransitions(link, keptRecords, nodesById) : {...link};
        nextLink.source_label = (nodesById.get(nextLink.source) || {}).label || nextLink.source_label;
        nextLink.target_label = (nodesById.get(nextLink.target) || {}).label || nextLink.target_label;
        if (Number(nextLink.value || 0) > 0) visibleLinks.push(nextLink);
      });

      const syntheticLinks = new Map();
      const transitions = [];
      (source.transitions || []).forEach(transition => {
        const junctionNode = sourceNodes.get(transition.junction);
        const sourceNode = sourceNodes.get(transition.source);
        const targetNode = sourceNodes.get(transition.target);
        const fromExcluded = isExcludedRslNode(sourceNode);
        const toExcluded = isExcludedRslNode(targetNode);
        if (fromExcluded && !toExcluded && targetNode && targetNode.kind === 'category') {
          const visibleJunctionNode = nodesById.get(junctionNode.id) || {...junctionNode, label: scopedMonthLabel(junctionNode)};
          nodesById.set(visibleJunctionNode.id, visibleJunctionNode);
          const syntheticNode = nodesById.get(syntheticDowngradeNodeId(sourceNode, junctionNode)) || makeSyntheticDowngradeNode(sourceNode, visibleJunctionNode);
          nodesById.set(syntheticNode.id, syntheticNode);
          const linkKey = flowKey(syntheticNode.id, visibleJunctionNode.id);
          const syntheticLink = syntheticLinks.get(linkKey) || makeSyntheticDowngradeLink(sourceNode, visibleJunctionNode, syntheticNode);
          mergeRecordTotals(syntheticLink, transition);
          syntheticLinks.set(linkKey, syntheticLink);
          transitions.push({...transition, id: `DWN-${transition.id}`, source: syntheticNode.id});
          return;
        }
        if (fromExcluded || toExcluded) return;
        transitions.push({...transition});
      });

      const links = [...visibleLinks, ...syntheticLinks.values()].filter(link => Number(link.value || 0) > 0);
      const usedNodeIds = new Set(links.flatMap(link => [link.source, link.target]));
      const nodes = recomputeScopedNodes(Array.from(nodesById.values()).filter(node => usedNodeIds.has(node.id)), links);

      return {
        ...source,
        metadata: {
          ...(source.metadata || {}),
          rsl_over_10m_display_scope: 'excluded_with_downgrade_source',
        },
        nodes,
        links,
        transitions,
      };
    }

    function displayDataForLens(lensKey) {
      const source = DATASETS.metrics[lensKey] || DATASETS.metrics[defaultLens];
      if (!source || !isRslGroupLensKey(lensKey) || includeRslOver10M) return source;
      return withoutDominantRslBucket(source);
    }

    function renderLegend() {
      const rslLens = isRslGroupLens();
      const splitExitLens = usesSecondarySalesExitSplit();
      const rslCategoryItems = includeRslOver10M
        ? [['>10M', '>10M'], ['6M–10M', '6M–10M'], ['3M–6M', '3M–6M'], ['<3M', '<3M'], ['Unknown', 'Unknown']]
        : [[RSL_DOWNGRADE_CATEGORY, RSL_DOWNGRADE_LABEL], ['6M–10M', '6M–10M'], ['3M–6M', '3M–6M'], ['<3M', '<3M'], ['Unknown', 'Unknown']];
      const mitigationExitItems = splitExitLens ? [['sec_sales', 'Sec Sales'], ['rotated_adjusted', 'Rotated/Adjusted']] : [['resolved', 'Resolved/Reduced']];
      const items = rslLens
        ? [['month', 'Month total hub'], ...rslCategoryItems, ['added', 'Added/Increased'], ...(splitExitLens ? mitigationExitItems : [['resolved', 'Reduced/Exited']])]
        : [['month', 'Month total hub'], ['sales_push', 'Sales push'], ['rotation', 'Rotation'], ['strategic_misc', 'Strategic Misc'], ['incorrect_stock', 'Incorrect stock'], ['tbc', 'TBC'], ['not_actionable', 'Not actionable'], ['added', 'Added/New'], ...mitigationExitItems];
      const color = {month:'#23395d', sales_push:'#2f80ed', rotation:'#9b51e0', incorrect_stock:'#f2994a', tbc:'#f2c94c', strategic_misc:'#8b5cf6', not_actionable:'#27ae60', added:'#eb5757', resolved:'#00a896', sec_sales:'#00A896', rotated_adjusted:'#64748B', sales_unavailable:'#94A3B8', ...RSL_COLORS};
      if ((DATA.nodes || []).some(node => node.kind === 'sales_unavailable')) items.push(['sales_unavailable', 'Sales unavailable']);
      d3.select(host.legend).selectAll('span').data(items).join('span')
        .html(d => `<i class="swatch${d[0] === 'sales_unavailable' ? ' hatch' : ''}" style="background:${color[d[0]]}"></i>${d[1]}`);
    }

    function valueCr(valueBdt) {
      return Number(valueBdt || 0) / host.unit.div;
    }

    function contributionRatio(valueBdt, denominatorBdt) {
      const denominator = Number(denominatorBdt || 0);
      return denominator > 0 ? Number(valueBdt || 0) / denominator : 0;
    }

    function valueContributionLabel(valueBdt, denominatorBdt) {
      return `${fmtValue(valueCr(valueBdt))} : ${fmtContribution(contributionRatio(valueBdt, denominatorBdt))}`;
    }

    function nodeContributionDenominator(d) {
      return d.contributionDenominator ?? d.displayValue ?? d.value;
    }

    function linkContributionDenominator(d) {
      return d.source && d.source.displayValue ? d.source.displayValue : d.displayValue ?? d.value;
    }

    function rslKey(value) {
      const text = String(value || '').trim();
      const compact = text.toLowerCase().replace(/\s+/g, '').replace(/–/g, '-');
      const map = {'>10m':'>10M', '>=10m':'>10M', '6m-10m':'6M–10M', '3m-6m':'3M–6M', '<3m':'<3M', unknown:'Unknown'};
      return map[compact] || (RSL_ORDER.includes(text) ? text : 'Unknown');
    }

    function rslRowsFromValues(values) {
      const totals = new Map(RSL_ORDER.map(group => [group, 0]));
      (values || []).forEach(row => {
        const group = rslKey(row[0]);
        totals.set(group, (totals.get(group) || 0) + Number(row[1] || 0));
      });
      return RSL_ORDER.map(group => ({group, label: RSL_LABELS[group] || group, value: totals.get(group) || 0})).filter(row => row.value > 0.01);
    }

    function exposureMixRowsFromValues(values) {
      const labels = ['Exposure', 'Non-exposure closing stock'];
      const totals = new Map(labels.map(label => [label, 0]));
      (values || []).forEach(row => {
        const label = labels.includes(String(row[0])) ? String(row[0]) : 'Non-exposure closing stock';
        totals.set(label, (totals.get(label) || 0) + Number(row[1] || 0));
      });
      return labels.map(label => ({label, value: totals.get(label) || 0})).filter(row => row.value > 0.01);
    }

    function brandRowsFromValues(values) {
      const totals = new Map();
      (values || []).forEach(row => {
        const brand = String(row[0] || 'Unknown brand').trim() || 'Unknown brand';
        totals.set(brand, (totals.get(brand) || 0) + Number(row[1] || 0));
      });
      return Array.from(totals, ([brand, value]) => ({brand, label: brand, value}))
        .filter(row => row.value > 0.01)
        .sort((a, b) => d3.descending(a.value, b.value) || d3.ascending(a.brand.toLowerCase(), b.brand.toLowerCase()))
        .slice(0, 5);
    }

    function gridRowsToHtml(className, title, rows, totalValue) {
      if (!rows || !rows.length) return '';
      const total = Number(totalValue || 0) || d3.sum(rows, row => row.value);
      let html = `<div class="${className}"><div><b>${title}</b></div><div></div>`;
      rows.forEach(row => {
        html += `<div title="${escapeHtml(row.label)}">${escapeHtml(row.label)}</div><div>${fmtValue(valueCr(row.value))} ${host.unit.label} / ${fmtContribution(contributionRatio(row.value, total))}</div>`;
      });
      return `${html}</div>`;
    }

    function tipGridsRowsToHtml(mixTitle, mixRows, brandRows, totalValue) {
      const grids = [
        gridRowsToHtml('rsl-tip-grid', mixTitle, mixRows, totalValue),
        gridRowsToHtml('brand-tip-grid', 'Top 5 brands', brandRows, totalValue),
      ].filter(Boolean);
      return grids.length ? `<div class="tip-grid-wrap">${grids.join('')}</div>` : '';
    }

    function moveTip() {
      tooltip.style('left', null).style('right', null).style('top', null).style('bottom', null);
    }

    function hideTip() { tooltip.style('opacity', 0); }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    function render() {
      hideDrilldownContextMenu();
      tooltip.style('opacity', 0);
      const svgNode = svg.node();
      const wrap = host.scroller;
      const rect = svgNode.getBoundingClientRect();
      const viewportWidth = Math.max(1100, (wrap && wrap.clientWidth) || svgNode.clientWidth || 1200);
      const dataColumns = Array.from(new Set((DATA.nodes || [])
        .map(d => Number(d.column))
        .filter(column => Number.isFinite(column))))
        .sort((a, b) => d3.ascending(a, b));
      const layoutColumnByDataColumn = new Map(dataColumns.map((column, index) => [column, index]));
      const columnCount = Math.max(1, dataColumns.length);
      // Reserve a label lane even at the tightest spacing and for the final column.
      const measureGroup = svg.append('g').attr('class', 'node').attr('visibility', 'hidden');
      const measureText = measureGroup.append('text');
      const labelWidth = Math.max(160, ...DATA.nodes.map(node => {
        measureText.text(node.label);
        return measureText.node().getComputedTextLength?.() || node.label.length * 6.6;
      }));
      measureGroup.remove();
      const rightMargin = labelWidth + 24;
      const columnSpacing = Math.max(horizontalSpacingPx, labelWidth + 24);
      const desiredWidth = Math.max(1100, (columnCount - 1) * columnSpacing + 44 + rightMargin);
      const width = Math.max(viewportWidth, desiredWidth);
      const height = host.layoutHeight || Math.max(760, rect.height || 820);
      svg.attr('width', width).attr('height', height).style('width', `${width}px`).attr('viewBox', [0, 0, width, height]);
      svg.classed('focus-locked', false);
      svg.selectAll('*').remove();
      /* FSNE host: filters plus hidden >10M can leave no nodes; d3-sankey cannot lay out an empty graph. */
      if (!DATA.nodes.length || !DATA.links.length) {
        controller.current = null;
        tooltip.style('opacity', 0);
        return;
      }
      svg.append('defs').html('<pattern id="rc-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#e2e8f0"/><rect width="2.5" height="6" fill="#94A3B8"/></pattern>');

      const displayTotals = new Map(DATA.nodes.map(d => [d.id, {in: 0, out: 0}]));
      DATA.links.forEach(link => {
        if (displayTotals.has(link.source)) displayTotals.get(link.source).out += link.value;
        if (displayTotals.has(link.target)) displayTotals.get(link.target).in += link.value;
      });

      const monthTotals = new Map();
      DATA.nodes.forEach(d => {
        if (d.kind !== 'month') return;
        const totals = displayTotals.get(d.id) || {in: 0, out: 0};
        monthTotals.set(d.month_key, Math.max(totals.in, totals.out));
      });

      function contributionMonthKey(d) {
        if (d.kind === 'added' || d.kind === 'rsl_downgrade') {
          const intakeLink = DATA.links.find(link => link.source === d.id && (nodeMetaById.get(link.target) || {}).kind === 'month');
          if (intakeLink) return nodeMetaById.get(intakeLink.target).month_key;
        }
        return d.month_key;
      }

      const graph = {
        nodes: DATA.nodes.map(d => {
          const totals = displayTotals.get(d.id) || {in: 0, out: 0};
          const displayValue = Math.max(totals.in, totals.out);
          const denominatorMonth = contributionMonthKey(d);
          const dataColumn = Number(d.column);
          const layoutColumn = layoutColumnByDataColumn.has(dataColumn) ? layoutColumnByDataColumn.get(dataColumn) : 0;
          return {...d, layoutColumn, displayValue, contributionDenominator: monthTotals.get(denominatorMonth) || displayValue};
        }),
        links: DATA.links.map(d => ({...d, displayValue: d.value, value: d.value * 0.5})),
      };

      const generator = d3.sankey()
        .nodeId(d => d.id)
        .nodeAlign((node) => node.layoutColumn)
        .nodeSort((a, b) => d3.ascending(a.layoutColumn, b.layoutColumn) || d3.ascending(a.sort, b.sort) || d3.ascending(a.label, b.label))
        /* FSNE host: stack bands by the policy order of the node at the far end (payload `sort`), not by size,
           so every month pillar reads Sales push, Rotation, Strategic Misc, ..., Added/New from the top. */
        .linkSort((a, b) => {
          const [x, y] = a.source === b.source ? [a.target, b.target] : [a.source, b.source];
          return d3.ascending(x.layoutColumn, y.layoutColumn) || d3.ascending(x.sort, y.sort) || d3.ascending(x.label, y.label) || d3.descending(a.value, b.value);
        })
        .nodeWidth(20)
        .nodePadding(28)
        .extent([[24, 30], [width - rightMargin, height - 38]])
        .iterations(48);

      generator(graph);
      /* D3 clamps alignment to the longest connected path. Filtered histories can have
         disconnected periods, so restore the calendar columns before packing labels. */
      const columnStep = (width - rightMargin - 24 - generator.nodeWidth()) / Math.max(1, columnCount - 1);
      graph.nodes.forEach(node => {
        node.layer = node.layoutColumn;
        node.x0 = 24 + node.layoutColumn * columnStep;
        node.x1 = node.x0 + generator.nodeWidth();
      });
      repackColumnsFromTop(graph, 82, nodeVisualPadding);
      generator.update(graph);

      const monthColumns = graph.nodes.filter(node => node.kind === 'month');
      const calendar = svg.append('g').attr('class', 'calendar-columns').attr('aria-hidden', 'true');
      calendar.selectAll('text').data(monthColumns).join('text')
        .attr('x', node => node.x0).attr('y', 30)
        .text(node => node.label.replace(/\s+opening.*$/i, ''));
      calendar.selectAll('line').data(monthColumns).join('line')
        .attr('x1', node => node.x0).attr('x2', node => node.x0)
        .attr('y1', 44).attr('y2', 64);

      const nodeById = new Map(graph.nodes.map(d => [d.id, d]));
      const linkByKey = new Map(graph.links.map(d => [`${d.source.id}|${d.target.id}`, d]));
      const projectedTransitions = (DATA.transitions || []).map(d => ({...d}));
      const transitionsByJunctionTarget = d3.group(projectedTransitions, d => `${d.junction}|${d.target}`);
      const transitionsByJunctionSource = d3.group(projectedTransitions, d => `${d.junction}|${d.source}`);
      let renderDetailsLoaded = false;

      async function ensureRenderDetails() {
        if (renderDetailsLoaded) return;
        if (host.detailsReady) await host.detailsReady();
        /* FSNE host: layout copies were taken before the lineage lists arrived, so copy them across by id */
        const lineageById = new Map([...(DATA.links || []), ...(DATA.transitions || [])].map(r => [r.id, r.lineage_values]));
        [...graph.links, ...projectedTransitions].forEach(r => {
          const lv = lineageById.get(r.id);
          if (lv && !(r.lineage_values && r.lineage_values.length)) { r.lineage_values = lv; delete r._lineageMap; }
        });
        renderDetailsLoaded = true;
      }

      function pairArrayToMap(values) {
        const out = new Map();
        (values || []).forEach(pair => {
          const key = Number(pair[0]);
          const value = Number(pair[1] || 0);
          if (value > 0) out.set(key, (out.get(key) || 0) + value);
        });
        return out;
      }

      function lineageMapFor(record) {
        if (!record._lineageMap) record._lineageMap = pairArrayToMap(record.lineage_values || []);
        return record._lineageMap;
      }

      function intersectMaps(left, right) {
        const out = new Map();
        left.forEach((value, key) => {
          const other = right.get(key) || 0;
          const selected = Math.min(value, other);
          if (selected > 0) out.set(key, selected);
        });
        return out;
      }

      function mapTotal(map) {
        let total = 0;
        map.forEach(value => { total += value; });
        return total;
      }

      function makeFocus(anchorValue, anchorNode) {
        return {links: new Set(), nodes: new Set(anchorNode ? [anchorNode] : []), linkMaps: new Map(), nodeValues: new Map(), anchorValue: Number(anchorValue || 0)};
      }

      function addFocusedLink(focus, focusedLink, amountMap) {
        if (!focusedLink || !amountMap || mapTotal(amountMap) <= 0) return;
        let existing = focus.linkMaps.get(focusedLink);
        if (!existing) {
          existing = new Map();
          focus.linkMaps.set(focusedLink, existing);
        }
        amountMap.forEach((value, key) => existing.set(key, Math.max(existing.get(key) || 0, value)));
        focus.links.add(focusedLink);
        focus.nodes.add(focusedLink.source);
        focus.nodes.add(focusedLink.target);
      }

      function traceUpstream(focus, focusedLink, selectedMap, depth) {
        if (!focusedLink || depth > 12 || mapTotal(selectedMap) <= 0) return;
        if (focusedLink.source.kind === 'month') {
          const transitions = transitionsByJunctionTarget.get(`${focusedLink.source.id}|${focusedLink.target.id}`) || [];
          transitions.forEach(transition => {
            const transitionMap = intersectMaps(lineageMapFor(transition), selectedMap);
            const parentLink = linkByKey.get(`${transition.source}|${transition.junction}`);
            const parentMap = parentLink ? intersectMaps(lineageMapFor(parentLink), transitionMap) : new Map();
            addFocusedLink(focus, parentLink, parentMap);
            traceUpstream(focus, parentLink, parentMap, depth + 1);
          });
          return;
        }

        (focusedLink.source.targetLinks || []).forEach(parentLink => {
          const parentMap = intersectMaps(lineageMapFor(parentLink), selectedMap);
          addFocusedLink(focus, parentLink, parentMap);
          traceUpstream(focus, parentLink, parentMap, depth + 1);
        });
      }

      function traceDownstream(focus, focusedLink, selectedMap, depth) {
        if (!focusedLink || depth > 12 || mapTotal(selectedMap) <= 0) return;
        if (focusedLink.target.kind === 'month') {
          const transitions = transitionsByJunctionSource.get(`${focusedLink.target.id}|${focusedLink.source.id}`) || [];
          transitions.forEach(transition => {
            const transitionMap = intersectMaps(lineageMapFor(transition), selectedMap);
            const childLink = linkByKey.get(`${transition.junction}|${transition.target}`);
            const childMap = childLink ? intersectMaps(lineageMapFor(childLink), transitionMap) : new Map();
            addFocusedLink(focus, childLink, childMap);
            traceDownstream(focus, childLink, childMap, depth + 1);
          });
          return;
        }

        (focusedLink.target.sourceLinks || []).forEach(childLink => {
          const childMap = intersectMaps(lineageMapFor(childLink), selectedMap);
          addFocusedLink(focus, childLink, childMap);
          traceDownstream(focus, childLink, childMap, depth + 1);
        });
      }

      function selectedValueForFocus(focus, d) {
        return mapTotal(focus.linkMaps.get(d) || new Map());
      }

      function selectedNodeValueForFocus(focus, d) {
        return focus.nodeValues.get(d) || 0;
      }

      function finalizeFocus(focus, anchorNode) {
        focus.nodes.forEach(node => {
          let incoming = 0;
          let outgoing = 0;
          (node.targetLinks || []).forEach(link => { if (focus.links.has(link)) incoming += selectedValueForFocus(focus, link); });
          (node.sourceLinks || []).forEach(link => { if (focus.links.has(link)) outgoing += selectedValueForFocus(focus, link); });
          const selected = Math.max(incoming, outgoing);
          if (selected > 0) focus.nodeValues.set(node, selected);
        });
        if (anchorNode) focus.nodeValues.set(anchorNode, focus.anchorValue);
        return focus;
      }

      function collectSliceFocusFromLink(anchor) {
        const anchorMap = lineageMapFor(anchor);
        const focus = makeFocus(anchor.displayValue ?? mapTotal(anchorMap), null);
        addFocusedLink(focus, anchor, anchorMap);
        traceUpstream(focus, anchor, anchorMap, 0);
        traceDownstream(focus, anchor, anchorMap, 0);
        return finalizeFocus(focus, null);
      }

      function collectSliceFocusFromNode(anchor) {
        const focus = makeFocus(anchor.displayValue ?? anchor.value, anchor);
        (anchor.targetLinks || []).forEach(parentLink => {
          const parentMap = lineageMapFor(parentLink);
          addFocusedLink(focus, parentLink, parentMap);
          traceUpstream(focus, parentLink, parentMap, 0);
        });
        (anchor.sourceLinks || []).forEach(childLink => {
          const childMap = lineageMapFor(childLink);
          addFocusedLink(focus, childLink, childMap);
          traceDownstream(focus, childLink, childMap, 0);
        });
        return finalizeFocus(focus, anchor);
      }

      function baseLinkWidth(d) {
        return Math.max(1, d.width * thicknessScale);
      }

      function focusedLinkWidth(d, selectedValue) {
        const widthPerBdt = d.displayValue > 0 ? (d.width * thicknessScale) / d.displayValue : 0;
        return Math.max(1.4, Math.min(baseLinkWidth(d), selectedValue * widthPerBdt));
      }

      function shadeHex(hex, factor) {
        const c = d3.color(hex || '#8da2c0') || d3.rgb('#8da2c0');
        const mix = value => factor < 0 ? value * (1 + factor) : value + (255 - value) * factor;
        return d3.rgb(mix(c.r), mix(c.g), mix(c.b)).formatHex();
      }

      function rslColor(baseColor, group) {
        return RSL_COLORS[rslKey(group)] || shadeHex(baseColor, 0);
      }

      function rslRowsForFocusedLink(focus, d) {
        const selectedMap = focus.linkMaps.get(d) || new Map();
        const selectedTotal = mapTotal(selectedMap);
        if (selectedTotal <= 0.01) return [];
        const byLineage = d3.group(host.lineageRows(d, 'rsl'), row => Number(row[0]));
        const totals = new Map(RSL_ORDER.map(group => [group, 0]));
        selectedMap.forEach((selected, lineageId) => {
          const rows = byLineage.get(Number(lineageId)) || [];
          const lineageTotal = d3.sum(rows, row => Number(row[2] || 0));
          rows.forEach(row => {
            const group = rslKey(row[1]);
            const amount = Number(row[2] || 0);
            const selectedAmount = lineageTotal > 0 ? selected * (amount / lineageTotal) : 0;
            totals.set(group, (totals.get(group) || 0) + selectedAmount);
          });
        });
        let rows = RSL_ORDER.map(group => ({group, label: RSL_LABELS[group] || group, value: totals.get(group) || 0})).filter(row => row.value > 0.01);
        if (!rows.length) {
          const fullRows = rslRowsFromValues(d.rsl_values);
          const fullTotal = d3.sum(fullRows, row => row.value);
          rows = fullRows.map(row => ({...row, value: fullTotal > 0 ? selectedTotal * (row.value / fullTotal) : 0})).filter(row => row.value > 0.01);
        }
        return rows;
      }

      function exposureMixRowsForFocusedLink(focus, d) {
        const selectedMap = focus.linkMaps.get(d) || new Map();
        const selectedTotal = mapTotal(selectedMap);
        if (selectedTotal <= 0.01) return [];
        const labels = ['Exposure', 'Non-exposure closing stock'];
        const byLineage = d3.group(host.lineageRows(d, 'exposure'), row => Number(row[0]));
        const totals = new Map(labels.map(label => [label, 0]));
        selectedMap.forEach((selected, lineageId) => {
          const rows = byLineage.get(Number(lineageId)) || [];
          const lineageTotal = d3.sum(rows, row => Number(row[2] || 0));
          rows.forEach(row => {
            const label = labels.includes(String(row[1])) ? String(row[1]) : 'Non-exposure closing stock';
            const amount = Number(row[2] || 0);
            const selectedAmount = lineageTotal > 0 ? selected * (amount / lineageTotal) : 0;
            totals.set(label, (totals.get(label) || 0) + selectedAmount);
          });
        });
        let rows = labels.map(label => ({label, value: totals.get(label) || 0})).filter(row => row.value > 0.01);
        if (!rows.length) {
          const fullRows = exposureMixRowsFromValues(d.exposure_mix_values);
          const fullTotal = d3.sum(fullRows, row => row.value);
          rows = fullRows.map(row => ({...row, value: fullTotal > 0 ? selectedTotal * (row.value / fullTotal) : 0})).filter(row => row.value > 0.01);
        }
        return rows;
      }

      function brandRowsForFocusedLink(focus, d) {
        const selectedMap = focus.linkMaps.get(d) || new Map();
        const selectedTotal = mapTotal(selectedMap);
        if (selectedTotal <= 0.01) return [];
        const byLineage = d3.group(host.lineageRows(d, 'brand'), row => Number(row[0]));
        const totals = new Map();
        selectedMap.forEach((selected, lineageId) => {
          const rows = byLineage.get(Number(lineageId)) || [];
          const lineageTotal = d3.sum(rows, row => Number(row[2] || 0));
          rows.forEach(row => {
            const brand = String(row[1] || 'Unknown brand').trim() || 'Unknown brand';
            const amount = Number(row[2] || 0);
            const selectedAmount = lineageTotal > 0 ? selected * (amount / lineageTotal) : 0;
            totals.set(brand, (totals.get(brand) || 0) + selectedAmount);
          });
        });
        let rows = Array.from(totals, ([brand, value]) => ({brand, label: brand, value}))
          .filter(row => row.value > 0.01)
          .sort((a, b) => d3.descending(a.value, b.value) || d3.ascending(a.brand.toLowerCase(), b.brand.toLowerCase()))
          .slice(0, 5);
        if (!rows.length) {
          const fullRows = brandRowsFromValues(d.brand_values);
          const fullTotal = d3.sum(fullRows, row => row.value);
          rows = fullRows.map(row => ({...row, value: fullTotal > 0 ? selectedTotal * (row.value / fullTotal) : 0})).filter(row => row.value > 0.01);
        }
        return rows;
      }

      function mergeRslRowsInto(target, rows) {
        rows.forEach(row => target.set(row.group, (target.get(row.group) || 0) + row.value));
      }

      function mergeBrandRowsInto(target, rows) {
        rows.forEach(row => target.set(row.brand, (target.get(row.brand) || 0) + row.value));
      }

      function mergeExposureMixRowsInto(target, rows) {
        rows.forEach(row => target.set(row.label, (target.get(row.label) || 0) + row.value));
      }

      function exposureMixRowsForFocusedNode(focus, d) {
        const labels = ['Exposure', 'Non-exposure closing stock'];
        const incoming = new Map(labels.map(label => [label, 0]));
        const outgoing = new Map(labels.map(label => [label, 0]));
        (d.targetLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeExposureMixRowsInto(incoming, exposureMixRowsForFocusedLink(focus, linkDatum)); });
        (d.sourceLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeExposureMixRowsInto(outgoing, exposureMixRowsForFocusedLink(focus, linkDatum)); });
        const incomingTotal = d3.sum(Array.from(incoming.values()));
        const outgoingTotal = d3.sum(Array.from(outgoing.values()));
        const chosen = incomingTotal >= outgoingTotal ? incoming : outgoing;
        let rows = labels.map(label => ({label, value: chosen.get(label) || 0})).filter(row => row.value > 0.01);
        if (!rows.length) rows = exposureMixRowsFromValues(d.exposure_mix_values);
        return rows;
      }

      function rslRowsForFocusedNode(focus, d) {
        const incoming = new Map(RSL_ORDER.map(group => [group, 0]));
        const outgoing = new Map(RSL_ORDER.map(group => [group, 0]));
        (d.targetLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeRslRowsInto(incoming, rslRowsForFocusedLink(focus, linkDatum)); });
        (d.sourceLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeRslRowsInto(outgoing, rslRowsForFocusedLink(focus, linkDatum)); });
        const incomingTotal = d3.sum(Array.from(incoming.values()));
        const outgoingTotal = d3.sum(Array.from(outgoing.values()));
        const chosen = incomingTotal >= outgoingTotal ? incoming : outgoing;
        let rows = RSL_ORDER.map(group => ({group, label: RSL_LABELS[group] || group, value: chosen.get(group) || 0})).filter(row => row.value > 0.01);
        if (!rows.length) rows = rslRowsFromValues(d.rsl_values);
        return rows;
      }

      function brandRowsForFocusedNode(focus, d) {
        const incoming = new Map();
        const outgoing = new Map();
        (d.targetLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeBrandRowsInto(incoming, brandRowsForFocusedLink(focus, linkDatum)); });
        (d.sourceLinks || []).forEach(linkDatum => { if (focus.links.has(linkDatum)) mergeBrandRowsInto(outgoing, brandRowsForFocusedLink(focus, linkDatum)); });
        const incomingTotal = d3.sum(Array.from(incoming.values()));
        const outgoingTotal = d3.sum(Array.from(outgoing.values()));
        const chosen = incomingTotal >= outgoingTotal ? incoming : outgoing;
        let rows = Array.from(chosen, ([brand, value]) => ({brand, label: brand, value}))
          .filter(row => row.value > 0.01)
          .sort((a, b) => d3.descending(a.value, b.value) || d3.ascending(a.brand.toLowerCase(), b.brand.toLowerCase()))
          .slice(0, 5);
        if (!rows.length) rows = brandRowsFromValues(d.brand_values);
        return rows;
      }

      function showFocusedLinkTip(event, d, focus) {
        const displayValue = selectedValueForFocus(focus, d);
        const denominator = focus && focus.anchorValue ? focus.anchorValue : linkContributionDenominator(d);
        const mixMode = activeTooltipMixMode();
        const mixRows = shouldShowTooltipMix()
          ? (mixMode === 'exposure_non_exposure' ? exposureMixRowsForFocusedLink(focus, d) : rslRowsForFocusedLink(focus, d))
          : [];
        const mixTitle = mixMode === 'exposure_non_exposure' ? 'Exposure vs Non-exposure Mix' : 'RSL mix';
        const brandRows = brandRowsForFocusedLink(focus, d);
        tooltip.html(`<h3>${valueContributionLabel(displayValue, denominator)}</h3>${tipGridsRowsToHtml(mixTitle, mixRows, brandRows, displayValue)}`).style('opacity', 1);
        moveTip(event);
      }

      function showFocusedNodeTip(event, d, focus) {
        const displayValue = selectedNodeValueForFocus(focus, d);
        const denominator = focus && focus.anchorValue ? focus.anchorValue : nodeContributionDenominator(d);
        const mixMode = activeTooltipMixMode();
        const mixRows = shouldShowTooltipMix()
          ? (mixMode === 'exposure_non_exposure' ? exposureMixRowsForFocusedNode(focus, d) : rslRowsForFocusedNode(focus, d))
          : [];
        const mixTitle = mixMode === 'exposure_non_exposure' ? 'Exposure vs Non-exposure Mix' : 'RSL mix';
        const brandRows = brandRowsForFocusedNode(focus, d);
        tooltip.html(`<h3>${escapeHtml(d.label)}</h3><div>${escapeHtml(d.kind)} · ${valueContributionLabel(displayValue, denominator)}</div>${tipGridsRowsToHtml(mixTitle, mixRows, brandRows, displayValue)}`).style('opacity', 1);
        moveTip(event);
      }

      function offsetVisualLinkPath(d, offset) {
        const x0 = d.source.x1;
        const x1 = d.target.x0;
        const y0 = visualLinkY(d.source, d.y0) + offset;
        const y1 = visualLinkY(d.target, d.y1) + offset;
        const xi = d3.interpolateNumber(x0, x1);
        const x2 = xi(0.5);
        return `M${x0},${y0}C${x2},${y0} ${x2},${y1} ${x1},${y1}`;
      }

      function renderRslOverlay(focus) {
        focusRslLayer.selectAll('*').remove();
        const focusedLinks = Array.from(focus.links).filter(linkDatum => selectedValueForFocus(focus, linkDatum) > 0.01);
        const segmentData = [];
        focusedLinks.forEach(linkDatum => {
          const rows = rslRowsForFocusedLink(focus, linkDatum);
          const total = d3.sum(rows, row => row.value);
          const width = focusedLinkWidth(linkDatum, selectedValueForFocus(focus, linkDatum));
          let cursor = -width / 2;
          rows.forEach(row => {
            const segmentWidth = Math.max(1, width * (row.value / total));
            const offset = cursor + segmentWidth / 2;
            cursor += segmentWidth;
            segmentData.push({linkDatum, group: row.group, width: segmentWidth, offset});
          });
        });
        focusRslLayer.append('g').selectAll('path')
          .data(segmentData)
          .join('path')
          .attr('d', row => offsetVisualLinkPath(row.linkDatum, row.offset))
          .attr('stroke', row => rslColor(linkColor(row.linkDatum), row.group))
          .attr('stroke-width', row => row.width)
          .attr('stroke-opacity', 1)
          .attr('fill', 'none')
          .attr('stroke-linecap', 'butt')
          .attr('pointer-events', 'none');

        const nodeSegments = [];
        focus.nodes.forEach(nodeDatum => {
          const rows = rslRowsForFocusedNode(focus, nodeDatum);
          const total = d3.sum(rows, row => row.value);
          if (total <= 0.01) return;
          const x = nodeDatum.x0;
          const width = nodeDatum.x1 - nodeDatum.x0;
          const y0 = visualNodeY0(nodeDatum);
          const height = Math.max(1, visualNodeY1(nodeDatum) - y0);
          let y = y0;
          rows.forEach(row => {
            const segmentHeight = Math.max(1, height * (row.value / total));
            nodeSegments.push({nodeDatum, group: row.group, x, y, width, height: segmentHeight});
            y += segmentHeight;
          });
        });
        focusRslLayer.append('g').selectAll('rect')
          .data(nodeSegments)
          .join('rect')
          .attr('x', row => row.x)
          .attr('y', row => row.y)
          .attr('width', row => row.width)
          .attr('height', row => row.height)
          .attr('fill', row => rslColor(row.nodeDatum.color, row.group))
          .attr('pointer-events', 'none');
      }

      function nodeDisplayValue(d, value, denominator) {
        const v = value ?? d.displayValue ?? d.value, den = denominator ?? nodeContributionDenominator(d);
        return host.pctOnly ? fmtContribution(contributionRatio(v, den)) : valueContributionLabel(v, den);
      }

      function focusLinkLabelData(focus) {
        return Array.from(focus.links).map(d => {
          const value = selectedValueForFocus(focus, d);
          const denominator = focus.anchorValue;
          return {link: d, value, denominator, text: fmtContribution(contributionRatio(value, denominator))};
        })
          .filter(d => d.value > 0 && d.denominator > 0)
          .sort((a, b) => d3.descending(a.value, b.value));
      }

      function rectsOverlap(a, b) {
        return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      }

      function labelRect(candidate) {
        const labelWidth = Math.max(40, candidate.text.length * 6.4 + 10);
        const labelHeight = 15;
        return {left: candidate.x - labelWidth / 2, right: candidate.x + labelWidth / 2, top: candidate.y - labelHeight + 3, bottom: candidate.y + 4};
      }

      function overlapArea(a, b) {
        if (!rectsOverlap(a, b)) return 0;
        return (Math.min(a.right, b.right) - Math.max(a.left, b.left)) * (Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      }

      function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
      }

      function candidateLabelPositions(linkDatum, text, pathMap) {
        const pathNode = pathMap.get(linkDatum);
        if (!pathNode || !pathNode.getTotalLength) {
          return [{x: (linkDatum.source.x1 + linkDatum.target.x0) / 2, y: (visualLinkY(linkDatum.source, linkDatum.y0) + visualLinkY(linkDatum.target, linkDatum.y1)) / 2 - 5, text, rank: 0}];
        }
        const length = pathNode.getTotalLength();
        const positions = [0.50, 0.38, 0.62, 0.25, 0.75, 0.13, 0.87];
        const offsets = [0, -16, 16, -30, 30, -44, 44];
        const candidates = [];
        positions.forEach((position, positionIndex) => {
          const p = pathNode.getPointAtLength(length * position);
          const before = pathNode.getPointAtLength(length * Math.max(0, position - 0.01));
          const after = pathNode.getPointAtLength(length * Math.min(1, position + 0.01));
          const dx = after.x - before.x;
          const dy = after.y - before.y;
          const mag = Math.hypot(dx, dy) || 1;
          const nx = -dy / mag;
          const ny = dx / mag;
          offsets.forEach((offset, offsetIndex) => {
            candidates.push({
              x: clamp(p.x + nx * offset, 28, width - 218),
              y: clamp(p.y + ny * offset, 34, height - 20),
              text,
              rank: positionIndex * 10 + offsetIndex,
            });
          });
        });
        return candidates;
      }

      function layoutFocusLabels(focus) {
        const pathMap = new Map();
        link.each(function(d) { pathMap.set(d, this); });
        const placed = [];
        return focusLinkLabelData(focus).map(row => {
          let best = null;
          let bestScore = Infinity;
          candidateLabelPositions(row.link, row.text, pathMap).forEach(candidate => {
            const rect = labelRect(candidate);
            let score = candidate.rank;
            placed.forEach(other => { score += overlapArea(rect, other) * 50; });
            if (rect.left < 4 || rect.right > width - 4 || rect.top < 4 || rect.bottom > height - 4) score += 100000;
            if (score < bestScore) {
              best = {...candidate, rect};
              bestScore = score;
            }
          });
          placed.push(best.rect);
          return {...row, x: best.x, y: best.y};
        });
      }

      function applyFocus(focus) {
        link.classed('active', d => focus.links.has(d))
          .classed('dimmed', d => !focus.links.has(d))
          .attr('stroke-width', d => focus.links.has(d) ? focusedLinkWidth(d, selectedValueForFocus(focus, d)) : baseLinkWidth(d))
          .attr('data-focus-value', d => focus.links.has(d) ? selectedValueForFocus(focus, d).toFixed(2) : null)
          .attr('data-focus-pct', d => focus.links.has(d) ? fmtContribution(contributionRatio(selectedValueForFocus(focus, d), focus.anchorValue)) : null);
        node.classed('active', d => focus.nodes.has(d))
          .classed('dimmed', d => !focus.nodes.has(d))
          .attr('data-focus-value', d => focus.nodes.has(d) ? selectedNodeValueForFocus(focus, d).toFixed(2) : null)
          .attr('data-focus-pct', d => focus.nodes.has(d) ? fmtContribution(contributionRatio(selectedNodeValueForFocus(focus, d), focus.anchorValue)) : null);
        node.select('tspan.node-value')
          .text(d => focus.nodes.has(d) ? nodeDisplayValue(d, selectedNodeValueForFocus(focus, d), focus.anchorValue) : nodeDisplayValue(d));
        focusLabelLayer.selectAll('text')
          .data(layoutFocusLabels(focus), d => d.link.id)
          .join('text')
          .attr('class', 'focus-label')
          .attr('x', d => d.x)
          .attr('y', d => d.y)
          .attr('text-anchor', 'middle')
          .text(d => d.text);
        if (isRslGroupLens()) focusRslLayer.selectAll('*').remove();
        else renderRslOverlay(focus);
        /* FSNE host: reparenting a hovered path loses pointerleave in Chrome. Only reorder locked slices. */
        if (lockedFocusState) link.filter(d => focus.links.has(d)).raise();
        focusLabelLayer.raise();
      }

      function clearFocus() {
        link.classed('active', false).classed('dimmed', false)
          .attr('stroke-width', baseLinkWidth)
          .attr('data-focus-value', null)
          .attr('data-focus-pct', null);
        node.classed('active', false).classed('dimmed', false)
          .attr('data-focus-value', null)
          .attr('data-focus-pct', null);
        node.select('tspan.node-value').text(d => nodeDisplayValue(d));
        focusRslLayer.selectAll('*').remove();
        focusLabelLayer.selectAll('*').remove();
      }

      const focusRslLayer = svg.append('g').attr('class', 'rsl-overlay-layer');
      const focusLabelLayer = svg.append('g').attr('class', 'focus-label-layer');
      let lockedFocusState = null;

      function clearLockedFocus() {
        const had = Boolean(lockedFocusState);
        lockedFocusState = null;
        svg.classed('focus-locked', false);
        clearFocus();
        hideTip();
        if (had && host.onLock) host.onLock(null);
      }

      function isSameLockedFocus(type, datum) {
        return lockedFocusState && lockedFocusState.type === type && lockedFocusState.datum === datum;
      }

      async function lockLinkFocus(event, d) {
        if (isSameLockedFocus('link', d)) {
          clearLockedFocus();
          return;
        }
        await ensureRenderDetails();
        if (controller.current && controller.current.graph !== graph) return; /* FSNE host: a newer render replaced this one */
        const focus = collectSliceFocusFromLink(d);
        lockedFocusState = {type: 'link', datum: d, focus};
        svg.classed('focus-locked', true);
        applyFocus(focus);
        showFocusedLinkTip(event, d, focus);
        if (host.onLock) host.onLock({id: d.id, type: 'link'});
      }

      async function lockNodeFocus(event, d) {
        if (isSameLockedFocus('node', d)) {
          clearLockedFocus();
          return;
        }
        await ensureRenderDetails();
        if (controller.current && controller.current.graph !== graph) return;
        const focus = collectSliceFocusFromNode(d);
        lockedFocusState = {type: 'node', datum: d, focus};
        svg.classed('focus-locked', true);
        applyFocus(focus);
        showFocusedNodeTip(event, d, focus);
        if (host.onLock) host.onLock({id: d.id, type: 'node'});
      }

      async function openSelectionMenu(event, d) {
        event.preventDefault();
        event.stopPropagation();
        hideTip();
        try {
          await controller.current.lockById(d.id);
          if (controller.current?.graph !== graph) return;
          hideTip();
          showDrilldownContextMenu(event, d);
        } catch (error) {
          tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error.message)}</div>`).style('opacity', 1);
        }
      }

      function selectionKeydown(event, d) {
        if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
          const rect = event.currentTarget.getBoundingClientRect();
          openSelectionMenu({clientX: rect.left + rect.width / 2, clientY: rect.top + Math.min(rect.height, 30), preventDefault() {}, stopPropagation() {}}, d);
          event.preventDefault();
          event.stopPropagation();
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          const lock = d.source ? lockLinkFocus : lockNodeFocus;
          lock(event, d).catch(error => tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error.message)}</div>`).style('opacity', 1));
        }
      }

      svg.on('click.focusLock', function(event) {
        hideDrilldownContextMenu();
        if (event.target === this) clearLockedFocus();
      });
      svg.on('pointerleave.focusHover', function() {
        if (!lockedFocusState) { clearFocus(); hideTip(); }
      }).on('pointerover.focusHover', function(event) {
        if (!lockedFocusState && !event.target.closest('.link, .node')) { clearFocus(); hideTip(); }
      });

      const link = svg.append('g').attr('fill', 'none').selectAll('path')
        .data(graph.links)
        .join('path')
        .attr('class', 'link')
        .attr('data-id', d => d.id)
        .attr('tabindex', 0)
        .attr('role', 'button')
        .attr('aria-label', d => `${d.source.label} → ${d.target.label}`)
        .on('keydown', selectionKeydown)
        .attr('d', visualLinkPath)
        .attr('stroke', linkColor)
        .attr('stroke-width', baseLinkWidth)
        .on('pointerenter', async function(event, d) { if (lockedFocusState) return; try { await ensureRenderDetails(); if (lockedFocusState || controller.current?.graph !== graph || !this.matches(':hover')) return; const focus = collectSliceFocusFromLink(d); applyFocus(focus); showFocusedLinkTip(event, d, focus); } catch (error) { tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error && error.message ? error.message : error)}</div>`).style('opacity', 1); } })
        .on('pointermove', function(event) { if (!lockedFocusState) moveTip(event); })
        .on('pointerleave', function() { if (lockedFocusState) return; clearFocus(); hideTip(); })
        .on('click', function(event, d) { event.stopPropagation(); hideDrilldownContextMenu(); lockLinkFocus(event, d).catch(error => tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error && error.message ? error.message : error)}</div>`).style('opacity', 1)); })
        .on('contextmenu', openSelectionMenu);

      const node = svg.append('g').selectAll('g')
        .data(graph.nodes)
        .join('g')
        .attr('class', 'node')
        .attr('data-id', d => d.id)
        .attr('tabindex', 0)
        .attr('role', 'button')
        .attr('aria-label', d => `${d.label}, ${nodeDisplayValue(d)}`)
        .on('keydown', selectionKeydown)
        .on('pointerenter', async function(event, d) { if (lockedFocusState) return; try { await ensureRenderDetails(); if (lockedFocusState || controller.current?.graph !== graph || !this.matches(':hover')) return; const focus = collectSliceFocusFromNode(d); applyFocus(focus); showFocusedNodeTip(event, d, focus); } catch (error) { tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error && error.message ? error.message : error)}</div>`).style('opacity', 1); } })
        .on('pointermove', function(event) { if (!lockedFocusState) moveTip(event); })
        .on('pointerleave', function() { if (lockedFocusState) return; clearFocus(); hideTip(); })
        .on('click', function(event, d) { event.stopPropagation(); hideDrilldownContextMenu(); lockNodeFocus(event, d).catch(error => tooltip.html(`<h3>Detail load failed</h3><div>${escapeHtml(error && error.message ? error.message : error)}</div>`).style('opacity', 1)); })
        .on('contextmenu', openSelectionMenu);

      node.append('rect')
        .attr('x', d => d.x0)
        .attr('y', visualNodeY0)
        .attr('height', d => Math.max(1, visualNodeY1(d) - visualNodeY0(d)))
        .attr('width', d => d.x1 - d.x0)
        .attr('fill', d => d.hatch ? 'url(#rc-hatch)' : (d.color || '#64748b'));

      const label = node.append('text')
        .attr('x', d => d.x0)
        .attr('y', nodeLabelY)
        .attr('text-anchor', 'start');

      label.append('tspan')
        .attr('x', d => d.x0)
        .text(d => d.label);

      label.append('tspan')
        .attr('class', 'node-value')
        .attr('x', d => d.x0)
        .attr('dy', `${labelLineGap}px`)
        .text(d => nodeDisplayValue(d));

      /* host additions: crop the svg to its content (layout above is unchanged), expose lock/describe */
      let contentBottom = 0;
      graph.nodes.forEach(d => { contentBottom = Math.max(contentBottom, nodeLabelY(d) + labelBlockHeight); });
      if (host.cropHeight) {
        const croppedHeight = Math.ceil(Math.max(host.minHeight || 420, contentBottom + 28));
        svg.attr('height', croppedHeight).style('height', `${croppedHeight}px`).attr('viewBox', [0, 0, width, croppedHeight]);
      }
      function describeLocked() {
        if (!lockedFocusState) return null;
        const {type, datum: d, focus} = lockedFocusState;
        const isLinkFocus = type === 'link';
        const value = isLinkFocus ? selectedValueForFocus(focus, d) : selectedNodeValueForFocus(focus, d);
        const rslRows = isLinkFocus ? rslRowsForFocusedLink(focus, d) : rslRowsForFocusedNode(focus, d);
        const brandRows = isLinkFocus ? brandRowsForFocusedLink(focus, d) : brandRowsForFocusedNode(focus, d);
        const mixRows = isLinkFocus ? exposureMixRowsForFocusedLink(focus, d) : exposureMixRowsForFocusedNode(focus, d);
        const sides = isLinkFocus ? [[d]] : [(d.targetLinks || []).filter(l => focus.links.has(l)), (d.sourceLinks || []).filter(l => focus.links.has(l))];
        const sideTotal = side => d3.sum(side, l => selectedValueForFocus(focus, l));
        const chosen = isLinkFocus ? sides[0] : (sideTotal(sides[0]) >= sideTotal(sides[1]) ? sides[0] : sides[1]);
        const lineage = new Map();
        chosen.forEach(l => (focus.linkMaps.get(l) || new Map()).forEach((v, k) => lineage.set(k, (lineage.get(k) || 0) + v)));
        return {
          id: d.id, type, label: isLinkFocus ? `${d.source.label} → ${d.target.label}` : d.label, kind: isLinkFocus ? 'link' : d.kind,
          color: isLinkFocus ? linkColor(d) : d.color, source: isLinkFocus ? d.source : null, target: isLinkFocus ? d.target : null, datum: d,
          value, anchorValue: focus.anchorValue, share: contributionRatio(value, focus.anchorValue),
          rslRows, brandRows, mixRows, lineage: Array.from(lineage, ([k, v]) => [Number(k), v]).sort((a, b) => b[1] - a[1]),
          nodeCount: focus.nodes.size, linkCount: focus.links.size,
        };
      }
      let pendingLock = null;
      async function lockById(id) {
        if (lockedFocusState && lockedFocusState.datum.id === id) return true;
        // concurrent restores must not toggle the lock off (lockNodeFocus/lockLinkFocus release on a repeat)
        if (pendingLock && pendingLock.id === id) return pendingLock.promise;
        const promise = lockByIdNow(id);
        pendingLock = {id, promise};
        try { return await promise; } finally { if (pendingLock && pendingLock.promise === promise) pendingLock = null; }
      }
      async function lockByIdNow(id) {
        const nodeDatum = nodeById.get(id);
        const linkDatum = nodeDatum ? null : graph.links.find(l => l.id === id);
        if (!nodeDatum && !linkDatum) return false;
        const fakeEvent = {clientX: 0, clientY: 0, stopPropagation() {}, preventDefault() {}};
        if (nodeDatum) await lockNodeFocus(fakeEvent, nodeDatum); else await lockLinkFocus(fakeEvent, linkDatum);
        return true;
      }
      controller.current = {graph, nodeById, width, height, lockById, clear: clearLockedFocus, describe: describeLocked,
        hasId: id => nodeById.has(id) || graph.links.some(l => l.id === id)};
    }

    function renderKeepingScroll() {
      const wrap = host.scroller;
      const maxBefore = wrap ? Math.max(1, wrap.scrollWidth - wrap.clientWidth) : 1;
      const ratio = wrap ? wrap.scrollLeft / maxBefore : 0;
      render();
      if (wrap) wrap.scrollLeft = ratio * Math.max(0, wrap.scrollWidth - wrap.clientWidth);
    }
    Object.assign(controller, {
      update(next) {
        activeLens = next.lens;
        includeRslOver10M = Boolean(next.includeOver10);
        horizontalSpacingPx = Math.max(160, Math.min(420, Math.round(Number(next.spacing) || 240)));
        DATA = displayDataForLens(activeLens);
        nodeMetaById = new Map(DATA.nodes.map(d => [d.id, d]));
        renderLegend();
        renderKeepingScroll();
        return DATA;
      },
      render: renderKeepingScroll,
      lockById: id => (controller.current ? controller.current.lockById(id) : Promise.resolve(false)),
      clear: () => { if (controller.current) controller.current.clear(); },
      data: () => DATA,
      isRslLens: isRslGroupLens,
    });
    return controller;
  } };
})(typeof window !== 'undefined' ? window : globalThis);
