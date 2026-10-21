# Ações manuais aguardando indefinidamente

Foram corrigidos três pontos de espera no código local:

- A prioridade das ações manuais fazia chamadas de segundo plano aguardarem indefinidamente. Agora é uma pausa curta e limitada; as filas de API continuam separadas. Isso evita a espera circular quando uma ação manual precisa de um recurso ocupado pela rotina de segundo plano.
- A espera por uma vaga de chamada à API agora tem prazo. Se o prazo acabar, a operação recebe uma mensagem explícita; a chamada que aguardava a vaga não é enviada. Uma operação com vários itens pode ter resultados parciais anteriores, portanto confira antes de repetir.
- Uma resposta externa com `Retry-After` muito longo não mantém um trabalhador parado por horas. A operação informa a espera solicitada, sem reenviar antecipadamente a mesma chamada.

As operações só entram no conjunto de ações interativas ativas quando começam de fato. O acompanhamento de cada operação informa idade e tempo desde o último progresso. `/api/health` também informa `oldest_active_job_seconds` e `longest_without_progress_seconds` para diagnóstico agregado. Uma operação longa não é declarada cancelada enquanto seu trabalhador ainda puder executá-la.

Esses problemas foram reproduzidos em testes locais. Ainda é necessário comparar os registros do servidor para confirmar a causa das travadas relatadas em produção.

## Registros do episódio de 21/10 antes das 9h de Brasília

O fim do intervalo abaixo equivale a 09:10 em Brasília. O parâmetro UTC torna a consulta independente do fuso configurado no servidor:

```bash
sudo journalctl -u competidor --utc --since '2026-10-21 03:00:00 UTC' --until '2026-10-21 12:10:00 UTC' --no-pager
sudo journalctl -t competidor-healthcheck --utc --since '2026-10-21 03:00:00 UTC' --until '2026-10-21 12:10:00 UTC' --no-pager
curl -sS --max-time 10 http://127.0.0.1:8770/api/health
sudo tail -n 40 /var/log/nginx/error.log
```

O `health` mostra o estado atual, não reconstrói o episódio anterior. Colete-o durante uma travada, se possível. Não repita publicações ou mudanças de preço sem conferir o resultado da primeira operação.

## Aplicação

Depois que o código estiver disponível no repositório, use a atualização habitual com `git pull --ff-only` e reinicie `competidor` em um momento sem operações importantes em andamento. Esta correção está em `server.py`, sem nova dependência e sem alteração obrigatória de Nginx ou RAM. O checkout local também contém as funcionalidades anteriores ainda não publicadas; a atualização pelo repositório aplica a versão nele disponível, não apenas este ajuste.
