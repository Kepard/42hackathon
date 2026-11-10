import { chromium, expect } from '@playwright/test';
const browser = await chromium.connectOverCDP('http://127.0.0.1:29229');
const page = await browser.contexts()[0].newPage();
let polls = 0, added = false;
const posts = [];
const task = {id:'offline_planks',description:'Craft planks',seed:12345,time_limit_seconds:120,inventory:[{id:'minecraft:oak_log',count:4}],goal:{crafted:'minecraft:oak_planks'}};
const investigation = {id:'c'.repeat(32),kind:'investigate',run_id:'mock-run',status:'running',status_detail:'waiting_for_user',done:true,url:'https://app.devin.ai/sessions/mock',result:{headline:'Offline investigation',failure_category:'stuck_position',root_cause:'No observed movement',confidence:.7,evidence:[{step:0,observation:'No progress'}],suggested_fixes:[{area:'prompt',change:'Recover using pixels',expected_effect:'Less repetition'}]}};
try {
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let data = [];
    if (path === '/api/status') data = {tasks:added ? ['get_log',task.id] : ['get_log']};
    if (path === '/api/runs/mock-run') data = {status:'finished',steps:[],summary:null,interventions:[]};
    if (path === '/api/devin/jobs' && request.method() === 'GET' && url.searchParams.has('run_id')) {
      if (++polls === 1) return route.fulfill({status:503,json:{detail:'temporary offline API'}});
      data = [investigation];
    }
    if (path === '/api/devin/jobs' && request.method() === 'POST') {
      const body = request.postDataJSON(); posts.push(body);
      data = body.kind === 'review_fix' ? {...investigation,id:'d'.repeat(32),kind:'review_fix',result:{pr_url:'https://github.com/Kepard/42hackathon/pull/999',summary:'Mock only',tests_added:'Mock',risk:'Mock'}} : {...investigation,id:'e'.repeat(32),kind:'challenge',result:{task,test_summary:'Offline mock',pr_url:'https://github.com/Kepard/42hackathon/pull/998'}};
    }
    if (path.endsWith('/add-task')) { added = true; data = task; }
    return route.fulfill({json:data});
  });
  await page.routeWebSocket('**/ws/live', ws => ws.send(JSON.stringify({type:'snapshot',data:{active_run:'mock-run',phase:'IDLE',connected:false,overlay:{}}})));
  page.on('dialog', dialog => dialog.accept());
  await page.goto('http://127.0.0.1:5173');
  const panel = page.getByRole('region',{name:'Devin engineering copilot'});
  await expect(panel.getByRole('alert')).toContainText('temporary offline API');
  await expect(panel.getByText('Offline investigation',{exact:true})).toBeVisible({timeout:15000});
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await panel.getByRole('button',{name:'Review Fix 1 · opens PR'}).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({kind:'review_fix',investigation_id:investigation.id,fix_index:0});
  await panel.getByLabel('Challenge description').fill('Craft planks');
  await panel.getByRole('button',{name:'Create Challenge · opens PR'}).click();
  await panel.getByRole('button',{name:'Add locally',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Added locally ✓'})).toBeDisabled();
  await expect(page.getByLabel('Task',{exact:true}).locator(`option[value="${task.id}"]`)).toHaveCount(1);
  expect(posts[1]).toMatchObject({kind:'challenge',description:'Craft planks',run_id:'mock-run'});
  console.log('PASS offline Devin UI: transient polling error clears; confirmed mocked Review Fix and Create Challenge; Add locally refreshes task selector; no real API calls');
} finally {await page.close(); await browser.close();}
