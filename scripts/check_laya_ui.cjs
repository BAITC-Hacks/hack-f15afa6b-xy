const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.argv[2]||'http://127.0.0.1:8769';
const mode=process.argv[3]||'laya';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.P109_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const create=async text=>{
    const response=await page.request.post(base+'/api/workspace/intake',{data:{text,region_id:'KZ-ALA',language:'ru',district:'Алмалинский',channel:'web'}});
    assert.equal(response.status(),201);return (await response.json()).id;
  };
  const open=async id=>{
    await page.locator('[data-action="refresh"]').click();
    await page.getByRole('searchbox',{name:'Поиск обращений'}).fill(id);
    await page.locator(`.queue-row[data-id="${id}"]`).click();
    await page.locator('#case-title').getByText(id,{exact:true}).waitFor();
  };
  try {
    await page.goto(base);
    await page.getByRole('searchbox',{name:'Поиск обращений'}).waitFor();
    if(mode==='laya') {
      const high=await create('На Абая 44 с утра нет холодной воды во всём доме');
      await open(high);
      await page.getByText('AI Classification · Laya',{exact:true}).waitFor();
      await page.getByText('Высокая уверенность · предварительный выбор, решение подтверждает оператор').waitFor();
      await page.locator('.routing-card').getByText('Айдана К.',{exact:true}).first().waitFor();
      await page.locator('[data-action="close"]').click();

      const medium=await create('Во дворе что-то течёт возле люка');
      await open(medium);
      await page.getByText(/Вторичная проверка Laya: не подтверждено/).waitFor();
      await page.getByText('НУЖНО УТОЧНЕНИЕ',{exact:true}).waitFor();
      await page.locator('[data-action="close"]').click();

      const fallback=await create('invalid-json, но на Абая нет холодной воды');
      await open(fallback);
      await page.getByText('AI Classification · DEMO FALLBACK',{exact:true}).waitFor();
      await page.getByText(/Laya недоступна/).waitFor();
      console.log('PASS LAYA UI 1: high confidence, clarification and visible offline fallback');
    } else {
      const disagreement=await create('disagreement: нет холодной воды');
      await open(disagreement);
      await page.getByText('AI Classification · Laya + classifier',{exact:true}).waitFor();
      await page.getByText(/Models disagree/).waitFor();
      await page.getByText(/Classifier: Водоснабжение 94% · Laya: Ливневая канализация 77%/).waitFor();
      console.log('PASS LAYA UI 2: hybrid disagreement shows both model proposals');
    }
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
