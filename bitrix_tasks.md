import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
};

const BITRIX_CLIENT_ID = Deno.env.get("BITRIX_CLIENT_ID");
const BITRIX_CLIENT_SECRET = Deno.env.get("BITRIX_CLIENT_SECRET");

serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  try {
    const supabaseClient = createClient(
      Deno.env.get('SUPABASE_URL') ?? '', 
      Deno.env.get('SUPABASE_ANON_KEY') ?? '',
      { auth: { persistSession: false } }
    );

    // Проверяем авторизацию
    const authHeader = req.headers.get('Authorization');
    if (!authHeader) {
      return new Response(JSON.stringify({
        error: 'No authorization header'
      }), {
        status: 401,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }

    const jwt = authHeader.replace('Bearer ', '');
    const { data: { user }, error: userError } = await supabaseClient.auth.getUser(jwt);
    
    if (userError || !user) {
      return new Response(JSON.stringify({
        error: 'Invalid token'
      }), {
        status: 401,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }

    // Получаем интеграцию пользователя с Битрикс24
    const { data: integration, error: integrationError } = await supabaseClient
      .from('bitrix_integrations')
      .select('*')
      .eq('user_id', user.id)
      .single();

    if (integrationError || !integration) {
      return new Response(JSON.stringify({
        error: 'Битрикс24 интеграция не найдена. Необходимо авторизоваться.'
      }), {
        status: 404,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }

    // Проверяем и обновляем токен если нужно
    const now = new Date();
    const expiresAt = new Date(integration.expires_at);
    
    let accessToken = integration.access_token;
    
    if (now >= expiresAt) {
      // Обновляем токен
      const refreshUrl = `https://${integration.domain}/oauth/token/`;
      const refreshParams = new URLSearchParams({
        grant_type: "refresh_token",
        client_id: BITRIX_CLIENT_ID!,
        client_secret: BITRIX_CLIENT_SECRET!,
        refresh_token: integration.refresh_token
      });

      const refreshResponse = await fetch(refreshUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: refreshParams.toString()
      });

      if (!refreshResponse.ok) {
        return new Response(JSON.stringify({
          error: 'Не удалось обновить токен. Необходимо повторно авторизоваться.'
        }), {
          status: 401,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      const newTokenData = await refreshResponse.json();
      accessToken = newTokenData.access_token;

      // Сохраняем новый токен
      await supabaseClient
        .from('bitrix_integrations')
        .update({
          access_token: newTokenData.access_token,
          refresh_token: newTokenData.refresh_token,
          expires_at: new Date(Date.now() + newTokenData.expires_in * 1000).toISOString()
        })
        .eq('user_id', user.id);
    }

    const { action, task_data } = await req.json();

    if (action === 'create_task') {
      // Создаем задачу в Битрикс24
      const taskUrl = `https://${integration.domain}/rest/tasks.task.add`;
      
      const taskParams = {
        fields: {
          TITLE: task_data.title,
          DESCRIPTION: task_data.description || '',
          RESPONSIBLE_ID: task_data.responsible_id || integration.bitrix_user_id,
          DEADLINE: task_data.deadline || null,
          PRIORITY: task_data.priority || '1', // 1-низкий, 2-средний, 3-высокий
          GROUP_ID: task_data.group_id || null,
          TAGS: task_data.tags || [],
          ...task_data.additional_fields
        },
        auth: accessToken
      };

      const taskResponse = await fetch(taskUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(taskParams)
      });

      const taskResult = await taskResponse.json();

      if (taskResult.error) {
        return new Response(JSON.stringify({
          error: 'Ошибка создания задачи в Битрикс24',
          details: taskResult.error_description
        }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      // Сохраняем информацию о задаче в Supabase (опционально)
      await supabaseClient
        .from('created_tasks')
        .insert({
          user_id: user.id,
          bitrix_task_id: taskResult.result.task.id,
          title: task_data.title,
          created_at: new Date().toISOString()
        });

      return new Response(JSON.stringify({
        success: true,
        task_id: taskResult.result.task.id,
        task_url: `https://${integration.domain}/workgroups/group/0/tasks/task/view/${taskResult.result.task.id}/`
      }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }

    if (action === 'get_users') {
      // Получаем список пользователей для назначения задач
      const usersUrl = `https://${integration.domain}/rest/user.get?auth=${accessToken}`;
      
      const usersResponse = await fetch(usersUrl);
      const usersResult = await usersResponse.json();

      if (usersResult.error) {
        return new Response(JSON.stringify({
          error: 'Ошибка получения пользователей',
          details: usersResult.error_description
        }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      const users = usersResult.result.map((user: any) => ({
        id: user.ID,
        name: `${user.NAME} ${user.LAST_NAME}`,
        email: user.EMAIL,
        photo: user.PERSONAL_PHOTO
      }));

      return new Response(JSON.stringify({
        success: true,
        users: users
      }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }

    return new Response(JSON.stringify({
      error: "Неизвестное действие"
    }), {
      status: 400,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });

  } catch (error) {
    return new Response(JSON.stringify({
      error: "Internal server error",
      details: error.message
    }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }
});