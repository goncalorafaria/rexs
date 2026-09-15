"""Exercise the real dashboard routing functions with delayed network responses."""
import shutil
import subprocess

import pytest

from rexs.web import APP_HTML


def test_navigation_ignores_stale_responses_and_timers(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is needed for browser JavaScript regression checks')
    script = APP_HTML.split('<script>')[1].split('</script>')[0]
    functions = '\n'.join(script[script.index(start):script.index(end)] for start, end in [
        ('function viewingExperiment(', 'function escapeHtml('),
        ('async function loadDetail(', 'function gpuSparkline('),
        ('async function renderRoute(', "document.getElementById('refresh-button').onclick"),
    ])
    harness = tmp_path / 'navigation.cjs'
    harness.write_text('''
const assert = require('node:assert/strict');
let routeVersion=0,detailRequestSequence=0,appliedDetailSequence=0,refreshTimer=null;
let logLineCount=200,experiments=[{}],app={innerHTML:''},errors=[],rendered=[],requests=[],timers=new Set();
const location={pathname:'/experiment/A'};
const history={replaceState(_,__,path){location.pathname=path;}};
const setError = value => { if(value) errors.push(value); };
const renderDetail = detail => rendered.push(detail);
const renderList = () => rendered.push('list');
const loadExperiments = async () => {};
const setInterval = fn => { timers.add(fn); return fn; };
const clearInterval = fn => timers.delete(fn);
const request = url => new Promise((resolve,reject) => requests.push({url,resolve,reject}));
''' + functions + '''
(async () => {
  const a=renderRoute();
  location.pathname='/experiment/B';
  const b=renderRoute();
  requests[1].resolve('B'); await b;
  requests[0].resolve('A'); await a;
  assert.deepEqual(rendered,['B']);
  assert.equal(timers.size,1,'old route must not install another interval');
  // An old request must not overwrite a newer response on the same page.
  const first=loadDetail('B',{quiet:true}), second=loadDetail('B',{quiet:true});
  requests[3].resolve('B-new'); await second;
  requests[2].resolve('B-old'); await first;
  assert.equal(rendered.at(-1),'B-new');
  // Returning to the same URL must not revive requests from its previous visit.
  const oldB=loadDetail('B',{quiet:true});
  location.pathname='/'; await renderRoute();
  location.pathname='/experiment/B'; const newB=renderRoute();
  requests[5].resolve('B-returned'); await newB;
  requests[4].reject(new Error('stale failure')); await oldB;
  assert.deepEqual(errors,[]);
  assert.equal(rendered.at(-1),'B-returned');
  assert.equal(timers.size,1);
  // A retired timer callback is harmless even before its response is requested.
  const count=requests.length;
  await loadDetail('A',{quiet:true});
  assert.equal(requests.length,count);
  // Navigating home while a detail request is pending keeps the list visible.
  const late=loadDetail('B',{quiet:true});
  location.pathname='/'; await renderRoute();
  requests.at(-1).resolve('B-late'); await late;
  assert.equal(rendered.at(-1),'list');
  assert.equal(timers.size,1);
})().catch(error => { console.error(error); process.exitCode=1; });
''')
    subprocess.run([node, str(harness)], check=True, capture_output=True, text=True, timeout=15)
