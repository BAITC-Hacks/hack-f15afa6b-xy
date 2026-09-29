const statuses={planned:'Запланировано',in_progress:'В работе',done:'Проверено'};
const outcomes={confirmed:'Рост подтверждён',false_alarm:'Ложный сигнал',data_issue:'Ошибка данных'};
const methods={last_value:'Последнее наблюдение',moving_average_3:'Среднее за 3 месяца',linear_trend:'Линейный тренд'};
const drafts=new Map();

export function registerAnalyticsActions(authFetch,esc) {
  customElements.define('pulse-analytics-actions',class extends HTMLElement {
    connectedCallback() {
      this.scope=Object.fromEntries(['data_origin','region_id','topic'].map(key=>[key,this.getAttribute(key)||null]));
      this.addEventListener('submit',event=>this.save(event));
      this.addEventListener('input',event=>this.remember(event));
      this.addEventListener('change',event=>this.remember(event));
      this.load();
    }

    disconnectedCallback() {this.controller?.abort();}

    async load(message='') {
      this.controller?.abort();
      this.controller=new AbortController();
      if(!this.data) this.innerHTML='<p role="status">Загружаем рекомендации…</p>';
      try {
        const params=new URLSearchParams(Object.entries(this.scope).filter(([,value])=>value));
        const response=await authFetch(`/api/analytics/actions?${params}`,{signal:this.controller.signal});
        if(!response.ok) throw new Error('Не удалось загрузить рекомендации. Повторите попытку.');
        this.data=await response.json();
        if(this.isConnected) this.render(message);
      } catch(error) {
        if(error.name==='AbortError') return;
        this.innerHTML='<p role="alert"></p><button type="button" class="button ghost">Повторить</button>';
        this.querySelector('p').textContent=error.message;
        this.querySelector('button').onclick=()=>this.load();
      }
    }

    remember(event) {
      const form=event.target.closest('form[data-review]');
      if(!form) return;
      const item=this.data.items.find(item=>item.id===form.dataset.review);
      const previous=drafts.get(item.id)||item.review;
      drafts.set(item.id,{...previous,status:form.elements.status.value,note:form.elements.note.value,
        outcome:form.elements.outcome?.value||null});
      this.syncForm(form);
    }

    syncForm(form) {
      const done=form.elements.status.value==='done';
      form.elements.note.required=done;
      const outcome=form.elements.outcome;
      if(outcome) {outcome.disabled=!done;outcome.required=done;outcome.closest('label').hidden=!done;}
    }

    async save(event) {
      const form=event.target.closest('form[data-review]');
      if(!form) return;
      event.preventDefault();event.stopPropagation();
      const id=form.dataset.review, item=this.data.items.find(item=>item.id===id);
      const draft=drafts.get(id)||item.review;
      const button=form.querySelector('button[type="submit"]'), notice=form.querySelector('[role="status"]');
      button.disabled=true;notice.textContent='Сохраняем…';
      try {
        const status=form.elements.status.value;
        const response=await authFetch(`/api/analytics/actions/${id}`,{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({...this.scope,
            revision:draft.revision,status,note:form.elements.note.value,
            outcome:status==='done'?(form.elements.outcome?.value||null):null})});
        const result=await response.json();
        if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'Проверьте статус и результат проверки.');
        drafts.delete(id);
        await this.load('Результат проверки сохранён.');
      } catch(error) {
        notice.textContent=error.message||'Не удалось сохранить. Ваш текст остался в форме.';
        if(error.message?.includes('Обновите')) {
          const reload=document.createElement('button');
          reload.type='button';reload.className='button ghost';reload.textContent='Загрузить актуальную запись';
          reload.onclick=()=>{drafts.delete(id);this.load();};
          notice.append(' ',reload);
        }
      } finally {button.disabled=false;}
    }

    card(item) {
      const review=drafts.get(item.id)||item.review;
      return `<article class="analytics-action" data-kind="${esc(item.kind)}">
        <div class="section-top"><h4>${esc(item.title)}</h4><span class="badge ${item.review.status==='done'?'green':'neutral'}">${esc(statuses[item.review.status])}</span></div>
        <p>${esc(item.evidence)}</p><ol>${item.steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol>
        <p class="action-measure"><strong>Как оценить результат:</strong> ${esc(item.measure)}</p>
        <details ${drafts.has(item.id)?'open':''}><summary>Записать ход проверки</summary>
          <form data-review="${esc(item.id)}">
            <div class="action-fields"><label>Статус<select name="status">${Object.entries(statuses).map(([key,label])=>`<option value="${key}" ${review.status===key?'selected':''}>${label}</option>`).join('')}</select></label>
            ${item.kind==='signal'?`<label>Результат проверки<select name="outcome"><option value="">Выберите результат</option>${Object.entries(outcomes).map(([key,label])=>`<option value="${key}" ${review.outcome===key?'selected':''}>${label}</option>`).join('')}</select></label>`:''}</div>
            <label>Что проверили и что меняем<textarea name="note" rows="3" maxlength="1000" placeholder="Вывод, согласованное действие и дата повторной проверки">${esc(review.note)}</textarea></label>
            <button class="button primary" type="submit">Сохранить результат</button><span role="status" aria-live="polite"></span>
          </form></details>
        ${item.review.updated_at?`<p class="micro">Сохранено ${esc(new Date(item.review.updated_at).toLocaleString(globalThis.pulseLocale||'ru-RU'))}</p>`:''}
      </article>`;
    }

    render(message) {
      const {items,feedback,comparison,history}=this.data;
      const signals=items.filter(item=>item.kind==='signal');
      const precision=feedback.reviewed_precision_percent;
      const candidateRows=Object.entries(comparison?.candidates||{}).sort((a,b)=>a[1].mae-b[1].mae);
      this.innerHTML=`<section class="analytics-actions" aria-labelledby="analytics-actions-title">
        <div class="section-top"><div><h3 id="analytics-actions-title">Проверки и рекомендации</h3></div>
        <span class="badge neutral">${items.filter(item=>item.review.status!=='done').length} ожидают проверки</span></div>
        <p class="micro">Сначала проверьте данные, затем причину всплеска и план нагрузки. Решения и результаты сохраняются для всей команды.</p>
        <p role="status" aria-live="polite">${esc(message)}</p>
        ${items.filter(item=>item.kind==='data').map(item=>this.card(item)).join('')}
        <div class="action-grid">${signals.slice(0,2).map(item=>this.card(item)).join('')}</div>
        ${signals.length>2?`<details class="action-more"><summary>Ещё ${signals.length-2} сигналов для проверки</summary><div class="action-grid">${signals.slice(2).map(item=>this.card(item)).join('')}</div></details>`:''}
        ${!signals.length?'<p class="micro">Для выбранных фильтров всплесков не найдено. Отдельные проблемы могут оставаться в очереди.</p>':''}
        ${items.filter(item=>item.kind==='forecast').map(item=>this.card(item)).join('')}
        <div class="action-quality"><h4>Результаты проверок</h4>
          <p>Проверки сигналов: <strong>${feedback.confirmed} подтверждено</strong> · ${feedback.false_alarm} ложных · ${feedback.data_issue} ошибок данных.</p>
          <p class="micro">${precision===null?'Доля подтверждённых пока неизвестна.':`Среди оценённых без ошибок данных подтверждено ${precision}%.`} Учтены последние сохранённые оценки сигналов по выбранным фильтрам за всё время. Непроверенные сигналы в расчёт не входят. ${this.scope.data_origin==='synthetic_demo'?'Результаты относятся к демонстрационным данным.':''}</p>
          <p class="micro">Сохранённые оценки используются для проверки сигналов. Порог срабатывания и модели автоматически не меняются.</p>
          ${comparison?`<details><summary>Сравнить методы прогноза</summary><div class="action-table"><table><caption>Проверка на прошлых месяцах · горизонт 3 месяца · ${comparison.evaluation.backtest_points} проверок</caption><thead><tr><th scope="col">Метод</th><th scope="col">MAE, обращений</th><th scope="col">sMAPE, %</th></tr></thead><tbody>${candidateRows.map(([key,row])=>`<tr><th scope="row">${esc(methods[key])}${key===comparison.method?' · выбран':''}</th><td>${row.mae}</td><td>${row.smape_percent}</td></tr>`).join('')}</tbody></table></div><p class="micro">Меньше ошибка — лучше. Метод выбран по MAE на этих же проверках; это не независимая оценка будущей точности. Повторите сравнение после нового полного месяца.</p></details>`:''}
        </div>
        ${history.length?`<details class="action-history"><summary>Сохранённые результаты · последние ${history.length}</summary>${history.map(row=>`<article><strong>${esc(row.title)} · ${esc(statuses[row.status])}</strong><p class="micro">${esc(row.evidence)}</p><p>${row.outcome?esc(outcomes[row.outcome])+'. ':''}${esc(row.note||'Без комментария')}</p><small>${esc(new Date(row.updated_at).toLocaleString(globalThis.pulseLocale||'ru-RU'))} · ${esc(row.actor)} · версия ${row.revision}</small></article>`).join('')}</details>`:''}
      </section>`;
      this.querySelectorAll('form[data-review]').forEach(form=>this.syncForm(form));
    }
  });
}
