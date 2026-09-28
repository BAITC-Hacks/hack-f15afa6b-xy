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
      await page.locator('.decision-section .field-label').getByText('Рекомендация ИИ',{exact:true}).waitFor();
      await page.getByText('Высокая уверенность · предварительный выбор, решение подтверждает оператор').waitFor();
      await page.locator('.routing-card').getByText('Айдана К.',{exact:true}).first().waitFor();
      await page.locator('[data-action="close"]').click();

      const medium=await create('Во дворе что-то течёт возле люка');
      await open(medium);
      await page.getByText(/Дополнительная проверка: категорию нужно проверить вручную/).waitFor();
      await page.getByText('НУЖНО УТОЧНЕНИЕ',{exact:true}).waitFor();
      await page.locator('[data-action="close"]').click();

      const fallback=await create('invalid-json, но на Абая нет холодной воды');
      await open(fallback);
      await page.getByText(/Основная рекомендация временно недоступна/).waitFor();
      console.log('PASS LAYA UI 1: plain-language recommendation, clarification and visible offline fallback');
    } else if(mode==='shadow') {
      const shadow=await create('На Абая 44 с утра нет холодной воды во всём доме');
      await open(shadow);
      await page.getByText(/Рекомендация ИИ · требуется проверка: Водоснабжение.*Маршрут автоматически не изменён/).waitFor();
      console.log('PASS LAYA UI 2: additional recommendation and non-interference are visible in plain language');
    } else {
      const disagreement=await create('disagreement: нет холодной воды');
      await open(disagreement);
      await page.getByText(/ИИ-сервисы дали разные рекомендации · проверьте категорию/).waitFor();
      await page.getByText(/Текущая рекомендация: Водоснабжение · Дополнительная рекомендация: Ливневая канализация/).waitFor();
      console.log('PASS LAYA UI 3: disagreement shows both recommendations without model names');
    }
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
