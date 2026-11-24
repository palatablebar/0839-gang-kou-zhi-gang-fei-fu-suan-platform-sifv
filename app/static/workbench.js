(function () {
  var state = { payload: null, kind: 'bars', run: null, approved: false, ticketText: '' };
  var $ = function (id) { return document.getElementById(id); };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setNotice(text) { $('notice').textContent = text || ''; }
  function renderIssues(data) {
    $('issue-count').textContent = data.issues.length + ' 条';
    $('issue-list').innerHTML = data.issues.length ? data.issues.map(function (item) {
      return '<li><span>' + esc(item.line || '-') + '</span><span>' + esc(item.key || '无主键') + '</span><code>' + esc(item.code) + '</code></li>';
    }).join('') : '<li class="empty">暂无异常</li>';
  }
  function renderBreakdown(items) {
    if (!items || !items.length) return '';
    return '<details><summary>' + items.length + ' 项</summary><ul>' + items.map(function (item) {
      return '<li>' + esc(item) + '</li>';
    }).join('') + '</ul></details>';
  }
  function renderRows(data) {
    var labels = Object.assign({label: '记录'}, state.resultColumns);
    var source = data.row_kind === 'source';
    var hasBreakdown = data.rows.some(function (row) { return row.breakdown && row.breakdown.length; });
    var keys = source ? state.inputColumns : Object.keys(labels).filter(function (key) {
      return data.rows.some(function (row) { return Object.hasOwn(row, key); });
    });
    $('row-count').textContent = source ? data.rows.length + ' 行预览' :
      data.summary.PASS + ' 通过 / ' + data.summary.REVIEW + ' 复核 / ' + data.summary.REJECT + ' 阻断';
    var tail = source ? '' : '<th>结论</th>' + (hasBreakdown ? '<th>计算分项</th>' : '');
    $('row-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key] || key) + '</th>'; }).join('') + tail + '</tr>';
    $('row-body').innerHTML = data.rows.length ? data.rows.map(function (row) {
      var values = keys.map(function (key) { return '<td class="' + (typeof row[key] === 'number' ? 'num' : '') + '">' + esc(row[key]) + '</td>'; }).join('');
      var verdict = source ? '' : '<td><span class="verdict" data-v="' + esc(row.verdict) + '">' + esc(row.verdict || '') + '</span><br><small>' + esc(row.reason || '') + '</small></td>';
      var breakdown = hasBreakdown ? '<td>' + renderBreakdown(row.breakdown) + '</td>' : '';
      return '<tr>' + values + verdict + breakdown + '</tr>';
    }).join('') : '<tr><td class="empty" colspan="' + (keys.length + (source ? 0 : 1)) + '">没有可展示条目</td></tr>';
  }
  function renderLedger(data) {
    if (!data.ledger) { $('ledger-list').innerHTML = '<p class="empty">运行对账后显示</p>'; return; }
    var values = data.ledger;
    $('ledger-list').innerHTML = [
      ['来源行', values.counts.input], ['采信行', values.counts.accepted], ['隔离行', values.counts.isolated],
      ['精确重复', values.counts.duplicates], ['忽略行', values.counts.ignored], ['计算条目', values.counts.computed]
    ].map(function (row) { return '<div class="ledger-row"><span>' + row[0] + '</span><strong>' + row[1] + '</strong></div>'; }).join('') +
      '<div class="ledger-row"><span>' + esc(values.metric_field) + '</span><strong>' + esc(values.metric_total) + '</strong></div>' +
      values.checks.map(function (check) { return '<div class="ledger-row"><span>' + esc(check.label) + '</span><strong class="' + (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>'; }).join('');
  }
  function renderTickets(data) {
    var tickets = data.tickets;
    var labels = state.tripColumns || {};
    var keys = Object.keys(labels);
    if (!tickets || !tickets.enabled) {
      $('ticket-count').textContent = '未提供港方票据';
      $('trip-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key]) + '</th>'; }).join('') + '</tr>';
      $('ticket-head').innerHTML = '';
      $('trip-body').innerHTML = '<tr><td class="empty" colspan="' + keys.length + '">未提供港方票据，逐趟金额未与票据对账</td></tr>';
      $('ticket-body').innerHTML = '';
      return;
    }
    var counts = tickets.counts || {};
    $('ticket-count').textContent = counts.trips + ' 趟 · ' + counts.posted + ' 张入账 · ' +
      counts.ambiguous + ' 张待认领 · ' + counts.unmatched + ' 张无对应';
    $('trip-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key]) + '</th>'; }).join('') + '</tr>';
    $('trip-body').innerHTML = (tickets.trips || []).map(function (row) {
      return '<tr>' + keys.map(function (key) {
        var value = key === 'status' ? (state.tripStates[row.status] || row.status) :
          key === 'ticket_id' ? (row.ticket_id || '—') :
          key === 'diff_cents' ? (row.diff_cents === null ? '—' : row.diff_cents) : row[key];
        var cls = key === 'status' ? (row.status === 'MATCHED' ? 'state-ok' : 'state-bad') : '';
        return '<td class="' + cls + '">' + esc(value) + '</td>';
      }).join('') + '</tr>';
    }).join('') || '<tr><td class="empty" colspan="' + keys.length + '">没有可对账趟次</td></tr>';
    var ticketLabels = state.ticketColumnsLabel;
    var ticketKeys = Object.keys(ticketLabels);
    $('ticket-head').innerHTML = '<tr>' + ticketKeys.map(function (key) { return '<th>' + esc(ticketLabels[key]) + '</th>'; }).join('') +
      '<th>状态</th><th>原因</th></tr>';
    $('ticket-body').innerHTML = (tickets.tickets || []).map(function (row) {
      var values = ticketKeys.map(function (key) { return '<td>' + esc(row[key]) + '</td>'; }).join('');
      var cls = row.status === 'POSTED' ? 'state-ok' : 'state-bad';
      return '<tr>' + values + '<td class="' + cls + '">' + esc(state.ticketStates[row.status] || row.status) +
        '</td><td><small>' + esc(row.reason || '') + '</small></td></tr>';
    }).join('') || '<tr><td class="empty" colspan="' + (ticketKeys.length + 2) + '">没有票据</td></tr>';
    var issues = (tickets.issues || []).slice(0, 6);
    if (issues.length) {
      $('ticket-body').innerHTML += issues.map(function (item) {
        return '<tr><td colspan="' + ticketLabels.length + '">第 ' + esc(item.line) + ' 行</td><td colspan="2"><small>' + esc(item.code) + '</small></td></tr>';
      }).join('');
    }
  }
  function render(data) {
    state.payload = data;
    $('chart-label').textContent = Object.values(state.resultColumns)[0];
    $('m-input').textContent = data.counts.input;
    $('m-accepted').textContent = data.counts.accepted;
    $('m-isolated').textContent = data.counts.isolated + data.counts.duplicates;
    $('m-rejected').textContent = data.summary.REJECT;
    renderRows(data);
    renderIssues(data);
    renderLedger(data);
    renderTickets(data);
    window.renderChart($('chart'), data.chart && data.chart.length ? (state.kind || 'bars') : 'bars', data.chart || []);
  }
  function stageButtons(stages) {
    $('stage-nav').innerHTML = stages.map(function (stage, index) {
      return '<button type="button" data-stage="' + stage.id + '" aria-pressed="' + (index === 0 ? 'true' : 'false') + '">' + esc(stage.name) + '</button>';
    }).join('');
    Array.from(document.querySelectorAll('[data-stage]')).forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-stage]').forEach(function (other) { other.setAttribute('aria-pressed', String(other === button)); });
        loadStage(button.getAttribute('data-stage'));
      });
    });
  }
  async function loadStage(stage) {
    try {
      setNotice('');
      document.body.setAttribute('aria-busy', 'true');
      var payload = await request('/api/stages/' + stage, {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      render(payload);
    } catch (error) { setNotice(error.message); }
    finally { document.body.removeAttribute('aria-busy'); }
  }
  async function runBatch() {
    try {
      setNotice('');
      $('run').disabled = true;
      var body = {profile: $('profile').value};
      if (state.ticketText.trim()) body.tickets = parseTickets(state.ticketText);
      var run = await request('/api/runs', {method: 'POST', body: JSON.stringify(body)});
      state.run = run;
      state.approved = run.status === 'APPROVED';
      render(run);
      $('run-status').textContent = run.status + ' · rev ' + run.revision;
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = run.summary.REJECT > 0 || state.approved || run.invalidated;
      $('hold').disabled = state.approved;
      $('ticket-late').disabled = false;
      $('download').hidden = !state.approved || run.invalidated;
      if (state.approved && !run.invalidated) $('download').href = '/api/runs/' + run.run_id + '/evidence';
      if (run.superseded_runs && run.superseded_runs.length) {
        setNotice('票据归属已更新，已使 ' + run.superseded_runs.length + ' 个旧批次的复核失效');
      }
    } catch (error) { setNotice(error.message); }
    finally { $('run').disabled = false; }
  }
  function parseTickets(text) {
    var lines = text.split(/\r?\n/).filter(function (line) { return line.trim(); });
    var labels = state.ticketColumnsLabel || {};
    var columns = Object.keys(labels).length ? Object.keys(labels) : ['ticket_id', 'container_id', 'gate_in', 'gate_out', 'amount_cents'];
    var headerWords = columns.concat(Object.values(labels), ['票号', '箱号', 'ticket_no', 'ctn_no', '金额']);
    var rows = [];
    var start = 0;
    if (lines.length) {
      var first = lines[0].toLowerCase();
      var isHeader = headerWords.some(function (word) { return first.indexOf(String(word).toLowerCase()) >= 0; });
      if (isHeader) start = 1;
    }
    for (var i = start; i < lines.length; i++) {
      var cells = lines[i].split(',').map(function (cell) { return cell.trim(); });
      var item = {};
      columns.forEach(function (column, index) { item[column] = cells[index] || ''; });
      rows.push(item);
    }
    return rows;
  }
  async function lateTickets() {
    if (!state.run) return;
    if (!state.ticketText.trim()) { setNotice('请先在复核意见中粘贴港方票据（表头加行）'); return; }
    try {
      var run = await request('/api/runs/' + state.run.run_id + '/tickets', {
        method: 'POST', body: JSON.stringify({tickets: parseTickets(state.ticketText)})
      });
      state.run = run;
      state.approved = run.status === 'APPROVED';
      render(run);
      $('run-status').textContent = run.status + ' · rev ' + run.revision;
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = run.summary.REJECT > 0;
      $('hold').disabled = false;
      $('download').hidden = true;
      setNotice('已按晚到票据重新对账' + (run.superseded_runs && run.superseded_runs.length ?
        '，旧批次的复核意见已失效' : ''));
    } catch (error) { setNotice(error.message); }
  }
  async function review(decision) {
    if (!state.run) return;
    try {
      var run = await request('/api/runs/' + state.run.run_id + '/review', {
        method: 'POST',
        body: JSON.stringify({decision: decision, comment: $('comment').value, expected_revision: state.run.revision})
      });
      state.run = run;
      state.approved = run.status === 'APPROVED';
      $('run-status').textContent = run.status + ' · rev ' + run.revision;
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = true;
      $('hold').disabled = true;
      $('download').hidden = !state.approved || run.invalidated;
      if (state.approved && !run.invalidated) $('download').href = '/api/runs/' + run.run_id + '/evidence';
    } catch (error) { setNotice(error.message); }
  }
  async function boot() {
    try {
      var catalog = await request('/api/catalog');
      state.kind = catalog.chart;
      state.resultColumns = catalog.result_columns;
      state.inputColumns = catalog.columns;
      state.tripColumns = catalog.trip_columns;
      state.ticketColumnsLabel = catalog.ticket_columns_label;
      state.ticketStates = catalog.ticket_states;
      state.tripStates = catalog.trip_states;
      stageButtons(catalog.stages.slice(0, 5));
      document.querySelector('.rail-note').textContent = catalog.code + ' · ' + catalog.stages[6].name;
      await loadStage('intake');
    } catch (error) { setNotice(error.message); }
    $('run').addEventListener('click', runBatch);
    $('ticket-late').addEventListener('click', lateTickets);
    $('ticket-text').addEventListener('input', function (event) { state.ticketText = event.target.value; });
    $('sign').addEventListener('click', function () { review('APPROVED'); });
    $('hold').addEventListener('click', function () { review('HELD'); });
    $('profile').addEventListener('change', function () {
      state.run = null; state.approved = false;
      $('sign').disabled = true; $('hold').disabled = true; $('download').hidden = true;
      $('ticket-late').disabled = true;
      $('run-status').textContent = '未执行批次'; $('revision').textContent = 'rev 0';
      loadStage('intake');
    });
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
