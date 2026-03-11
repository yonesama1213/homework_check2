import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date
import io
import chardet
import os
import unicodedata

# --- 1. データベース設定 & ユーティリティ (Supabase版) ---
# .streamlit/secrets.toml から読み込みます
try:
    url: str = st.secrets["supabase"]["url"]
    key: str = st.secrets["supabase"]["key"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error("接続設定が見つかりません。.streamlit/secrets.toml を確認してください。")
    st.stop()

def to_hankaku(text):
    """全角英数字・記号を半角に変換"""
    if text is None: return ""
    return unicodedata.normalize('NFKC', str(text).strip())

def fix_class_name(c):
    """クラス名に「組」を補完し、半角化する"""
    c = to_hankaku(c)
    if c and not c.endswith("組"): return f"{c}組"
    return c

# 注: init_db (テーブル作成) は Supabase の SQL Editor で行うため、
# アプリ起動時の自動実行は省略し、接続確認のみ行います。

# --- 2. デザイン & レイアウト調整 ---
st.set_page_config(page_title="課題チェック", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    /* サイドバー固定設定 */
    [data-testid="collapsedControl"] { display: none; }
    section[data-testid="stSidebar"] > div { padding-top: 0rem !important; }
    
    /* メイン画面の余白調整 */
    .block-container { padding-top: 4rem !important; padding-bottom: 0rem; }
    
    /* 全体のフォントサイズ調整 */
    html, body, [class*="ViewContainer"] { font-size: 14px; }
    .stMarkdown p, .stMarkdown li { font-size: 14px !important; }
    .stButton button { font-size: 13px !important; padding: 0.2rem 0.5rem; }
    h1 { font-size: 24px !important; margin-bottom: 15px !important; }
    h2 { font-size: 18px !important; }
    .stDataFrame div { font-size: 13px !important; }
    </style>
""", unsafe_allow_html=True)

# セッション状態の初期化
states = ['edit_email', 'edit_student_email', 'edit_hr_key', 'selected_hr', 'hr_sub_page', 'selected_course', 'course_sub_page']
for s in states:
    if s not in st.session_state: st.session_state[s] = None

# --- 3. サイドバー構成 --- (75行目あたりから差し替え)
st.sidebar.markdown("# 課題チェック")
user_input_email = st.sidebar.text_input("ログインメール", placeholder="example@midorigls.onmicrosoft.com", key="login")

if user_input_email:
    SUPER_ADMIN = "t.yonezawa@midorigls.onmicrosoft.com"
    # 検索用に正規化（半角・すべて小文字化）
    user_email = to_hankaku(user_input_email).lower()
    
    # 役割判定 (大文字小文字を区別しない .ilike を使用)
    admin_res = supabase.table("admins").select("*").ilike("email", user_email).execute()
    stu_res = supabase.table("students").select("*").ilike("email", user_email).execute()
    
    is_teacher = len(admin_res.data) > 0
    is_student = len(stu_res.data) > 0

    # 【救済処置】米澤先生のアドレスなら、DB登録がなくても強制的に教員(管理者)にする
    if user_email == "t.yonezawa@midorigls.onmicrosoft.com":
        is_teacher = True
    
    # 表示名の設定
    if is_teacher and len(admin_res.data) > 0:
        u = admin_res.data[0]
        current_user_full_name = f"{u['last_name']} {u['first_name']}"
    elif is_student and len(stu_res.data) > 0:
        u = stu_res.data[0]
        current_user_full_name = f"{u['last_name']} {u['first_name']}"
    elif user_email == "t.yonezawa@midorigls.onmicrosoft.com":
        current_user_full_name = "米澤 泰佑 (管理者)"
    else: 
        current_user_full_name = "未登録ユーザー"

    all_t_data = supabase.table("admins").select("last_name, first_name").execute()
    teacher_options = ["なし"] + [f"{r['last_name']} {r['first_name']}" for r in all_t_data.data]

    # メニュー名の定義
    m_home, m_task_reg, m_hr, m_student, m_course, m_teacher = "🏠 ホーム", "📝 課題登録", "🏫 HR管理", "👥 生徒管理", "📖 授業管理", "👨‍🏫 教員管理"
    
    # --- 権限によるメニューの切り出し ---
    if is_teacher:
        if user_email == "t.yonezawa@midorigls.onmicrosoft.com":
            # 管理者はすべてのメニューを表示
            menu_list = [m_home, m_task_reg, m_hr, m_student, m_course, m_teacher]
        else:
            # 一般教員は教員管理以外を表示
            menu_list = [m_home, m_task_reg, m_hr, m_student, m_course]
    elif is_student:
        menu_list = [m_home, m_task_reg]
    else:
        menu_list = [m_home]
    
    st.sidebar.markdown("---")
    st.sidebar.write(f"**{current_user_full_name} さん**")
    sel_menu = st.sidebar.radio("menu_nav", menu_list, label_visibility="collapsed")
    def get_csv_template(cols):
        df = pd.DataFrame(columns=cols)
        buf = io.BytesIO(); df.to_csv(buf, index=False, encoding='utf-8-sig')
        return buf.getvalue()

    # --- 🏠 ホーム ---
    if sel_menu == m_home:
        st.header("課題・提出物一覧")
        display_data = []
        
        try:
            # 課題データの取得
            t_q = supabase.table("assignments").select("*, courses_info(name, teacher_name)").execute()
            for r in t_q.data:
                show = False
                if is_teacher:
                    # 自分が担当、もしくは自分が担任/副担のHR
                    if r['courses_info'] and current_user_full_name in r['courses_info']['teacher_name']:
                        show = True
                    elif r['hr_key']:
                        h_split = r['hr_key'].split('_')
                        h_res = supabase.table("class_master").select("*").eq("grade", h_split[0]).eq("class_name", h_split[1]).execute()
                        if h_res.data:
                            h = h_res.data[0]
                            if h['teacher_name'] == current_user_full_name or h['sub_teacher_name'] == current_user_full_name: show = True
                else:
                    # 生徒本人の履修科目の課題、または本人のクラスのHR
                    if r['course_id']:
                        uc_check = supabase.table("user_courses").select("course_id").eq("user_id", user_email).eq("course_id", r['course_id']).execute()
                        if len(uc_check.data) > 0: show = True
                    elif r['hr_key']:
                        my_hr_key = f"{stu_res.data[0]['grade']}_{stu_res.data[0]['class']}" if is_student else ""
                        if r['hr_key'] == my_hr_key: show = True
                
                if show:
                    display_data.append({
                        "id": r['id'],
                        "分類": "授業" if r['course_id'] else "HR",
                        "対象": r['courses_info']['name'] if r['courses_info'] else r['hr_key'].replace('_', '年'),
                        "課題名": r['title'],
                        "提出期限": r['deadline'],
                        "内容": r['description']
                    })
        except Exception:
            st.error("データの取得中にエラーが発生しました。SQL Editorでリレーション設定を確認してください。")

        my_tasks_list = pd.DataFrame(display_data)
        if not my_tasks_list.empty:
            my_tasks_list = my_tasks_list.sort_values("提出期限")
            if is_teacher:
                my_tasks_list.insert(0, "削除選択", False)
                ed_home = st.data_editor(my_tasks_list, hide_index=True, use_container_width=True, column_config={"id": None})
                sel_ids = ed_home[ed_home["削除選択"] == True]["id"].tolist()
                if st.button(f"選択した {len(sel_ids)} 件を削除", type="primary", disabled=len(sel_ids)==0):
                    for tid in sel_ids:
                        supabase.table("assignments").delete().eq("id", tid).execute()
                        supabase.table("task_submissions").delete().eq("assignment_id", tid).execute()
                    st.rerun()
            else: st.dataframe(my_tasks_list.drop(columns=['id']), hide_index=True, use_container_width=True)
        else: st.info("現在登録されている課題や提出物はありません。")

    # --- 📝 課題登録 ---
    elif sel_menu == m_task_reg:
        st.header(m_task_reg)
        task_type = st.radio("登録する種類", ["授業の課題", "HRの提出物"], horizontal=True) #
        
        with st.form("add_task_form_dynamic", clear_on_submit=True):
            target_option = None
            if task_type == "授業の課題":
                if is_teacher:
                    c_res = supabase.table("courses_info").select("id, grade, name").eq("teacher_name", current_user_full_name).execute()
                else:
                    uc_res = supabase.table("user_courses").select("course_id").eq("user_id", user_email).execute()
                    c_ids = [r['course_id'] for r in uc_res.data]
                    c_res = supabase.table("courses_info").select("id, grade, name").in_("id", c_ids).execute()
                df_opt = pd.DataFrame(c_res.data)
                if not df_opt.empty:
                    df_opt["display"] = df_opt["grade"] + "年 " + df_opt["name"]
                    target_option = st.selectbox("対象の授業を選択", df_opt["display"].tolist())
            else:
                if is_teacher:
                    h_res = supabase.table("class_master").select("*").or_(f"teacher_name.eq.{current_user_full_name},sub_teacher_name.eq.{current_user_full_name}").execute()
                else:
                    h_res = supabase.table("class_master").select("*").eq("grade", stu_res.data[0]['grade']).eq("class_name", stu_res.data[0]['class']).execute()
                df_opt = pd.DataFrame(h_res.data)
                if not df_opt.empty:
                    df_opt["display"] = df_opt["grade"] + "年" + df_opt["class_name"]
                    target_option = st.selectbox("対象のクラスを選択", df_opt["display"].tolist())

            task_t = st.text_input("タイトル"); task_d = st.date_input("提出期限", value=date.today()); task_m = st.text_area("詳細・メモ")
            if st.form_submit_button("課題を登録する"):
                if task_t and target_option:
                    if task_type == "授業の課題":
                        cid = int(df_opt[df_opt["display"] == target_option]["id"].values[0])
                        supabase.table("assignments").insert({"course_id": cid, "title": task_t, "deadline": str(task_d), "description": task_m}).execute()
                    else:
                        hr_key = f"{df_opt[df_opt['display']==target_option]['grade'].values[0]}_{df_opt[df_opt['display']==target_option]['class_name'].values[0]}"
                        supabase.table("assignments").insert({"hr_key": hr_key, "title": task_t, "deadline": str(task_d), "description": task_m}).execute()
                    st.success(f"{task_type} を登録しました。"); st.rerun()

    # --- 🏫 HR管理 ---
    elif sel_menu == m_hr:
        st.header(m_hr)
        if st.session_state.selected_hr:
            sel_g, sel_c = st.session_state.selected_hr.split('_')
            if st.button("← クラス一覧に戻る"): st.session_state.selected_hr = None; st.session_state.hr_sub_page = None; st.rerun()
            res = supabase.table("class_master").select("*").eq("grade", sel_g).eq("class_name", sel_c).execute()
            if res.data:
                h = res.data[0]
                st.subheader(f"🏫 {h['grade']}年{h['class_name']} 詳細 (担任: {h['teacher_name']} / 副担: {h['sub_teacher_name']})")
                b_row = st.columns(4)
                if b_row[0].button("HR削除"): supabase.table("class_master").delete().eq("grade", sel_g).eq("class_name", sel_c).execute(); st.session_state.selected_hr = None; st.rerun()
                if b_row[1].button("HR編集"): st.session_state.hr_sub_page = "hr_edit"; st.rerun()
                if b_row[2].button("生徒登録"): st.session_state.hr_sub_page = "stu_reg"; st.rerun()
                if b_row[3].button("生徒選択削除"): st.session_state.hr_sub_page = "stu_del"; st.rerun()
                st.divider()
                if st.session_state.hr_sub_page == "hr_edit":
                    with st.form("ed_hr"):
                        new_g = st.text_input("学年", value=h['grade']); new_c = st.text_input("組", value=h['class_name']); nt = st.selectbox("担任", teacher_options, index=teacher_options.index(h['teacher_name']) if h['teacher_name'] in teacher_options else 0); nst = st.selectbox("副担", teacher_options, index=teacher_options.index(h['sub_teacher_name']) if h['sub_teacher_name'] in teacher_options else 0)
                        if st.form_submit_button("保存"): supabase.table("class_master").update({"grade":to_hankaku(new_g), "class_name":fix_class_name(new_c), "teacher_name":nt, "sub_teacher_name":nst}).eq("grade", sel_g).eq("class_name", sel_c).execute(); st.session_state.selected_hr = f"{to_hankaku(new_g)}_{fix_class_name(new_c)}"; st.session_state.hr_sub_page = None; st.rerun()
                elif st.session_state.hr_sub_page == "stu_reg":
                    t_indiv, t_csv = st.tabs(["個別", "CSV"])
                    with t_indiv:
                        with st.form("ad_s_i", clear_on_submit=True):
                            sm = st.text_input("メールアドレス")
                            c1, c2 = st.columns(2)
                            sl = c1.text_input("姓"); sf = c2.text_input("名")
                            slf = c1.text_input("姓（フリガナ）"); sff = c2.text_input("名（フリガナ）")
                            sn = st.text_input("出席番号")
                            if st.form_submit_button("登録"):
                                supabase.table("students").upsert({
                                    "email": to_hankaku(sm).lower(), 
                                    "last_name": sl, "first_name": sf, 
                                    "last_name_furi": slf, "first_name_furi": sff,
                                    "grade": sel_g, "class": sel_c, "number": to_hankaku(sn)
                                }).execute()
                                st.success("登録完了")
                    with t_csv:
                        # テンプレートの項目を更新
                        st.download_button("📥 テンプレート", data=get_csv_template(["メールアドレス", "姓", "名", "姓フリガナ", "名フリガナ", "出席番号"]), file_name="stu_temp.csv")
                        up_s = st.file_uploader("生徒CSVを選択", type="csv")
                        if up_s and st.button("一括登録実行"):
                            df = pd.read_csv(io.BytesIO(up_s.read()))
                            for _, r in df.iterrows():
                                supabase.table("students").upsert({
                                    "email": to_hankaku(str(r[0])).lower(), 
                                    "last_name": str(r[1]), "first_name": str(r[2]), 
                                    "last_name_furi": str(r[3]), "first_name_furi": str(r[4]),
                                    "grade": sel_g, "class": sel_c, "number": to_hankaku(str(r[5]))
                                }).execute()
                            st.success("一括登録が完了しました"); st.rerun()
                elif st.session_state.hr_sub_page == "stu_del":
                    s_res = supabase.table("students").select("*").eq("grade", sel_g).eq("class", sel_c).execute()
                    df_d = pd.DataFrame(s_res.data)
                    if not df_d.empty:
                        df_d.insert(0, "選択", False); ed = st.data_editor(df_d, hide_index=True, use_container_width=True, column_config={"email":None})
                        sel = ed[ed["選択"]==True]["email"].tolist()
                        if st.button("削除実行"): [supabase.table("students").delete().eq("email", m).execute() for m in sel]; st.session_state.hr_sub_page = None; st.rerun()
                
                s_list = supabase.table("students").select("number, last_name, first_name, email").eq("grade", sel_g).eq("class", sel_c).execute()
                if s_list.data: st.dataframe(pd.DataFrame(s_list.data).sort_values("number"), hide_index=True, use_container_width=True)
        else:
            h1, h2 = st.tabs(["一覧", "新規"])
            with h1:
                h_res = supabase.table("class_master").select("*").order("grade").order("class_name").execute()
                df_h = pd.DataFrame(h_res.data)
                for g in sorted(df_h['grade'].unique() if not df_h.empty else []):
                    st.write(f"#### {g}学年"); g_cls = df_h[df_h['grade']==g]; cols = st.columns(4)
                    for i, (_, r) in enumerate(g_cls.iterrows()):
                        with cols[i%4]:
                            with st.container(border=True):
                                st.write(f"**{r['grade']}年{fix_class_name(r['class_name'])}**")
                                st.write(f"担任: {r['teacher_name']}")
                                if st.button("詳細", key=f"hb_{r['grade']}_{r['class_name']}", use_container_width=True): st.session_state.selected_hr = f"{r['grade']}_{r['class_name']}"; st.rerun()
            with h2:
                with st.form("n_hr"):
                    gi = st.selectbox("学年", ["1", "2", "3"]); ci = st.text_input("組"); ti = st.selectbox("担任", teacher_options); si = st.selectbox("副担", teacher_options)
                    if st.form_submit_button("登録"): supabase.table("class_master").insert({"grade":to_hankaku(gi), "class_name":fix_class_name(ci), "teacher_name":ti, "sub_teacher_name":si}).execute(); st.rerun()

    # --- 👥 生徒管理 ---
    elif sel_menu == m_student:
        st.header(m_student); st.subheader("生徒検索・編集")
        f1, f2 = st.columns(2); gf = f1.selectbox("学年", ["すべて", "1", "2", "3"])
        cl_res = supabase.table("students").select("class").execute()
        cl_list = sorted(list(set([r['class'] for r in cl_res.data])))
        cf = f2.selectbox("クラス", ["すべて"] + cl_list)
        q = supabase.table("students").select("*")
        if gf != "すべて": q = q.eq("grade", gf)
        if cf != "すべて": q = q.eq("class", cf)
        df_s = pd.DataFrame(q.execute().data)
        if not df_s.empty:
            for _, r in df_s.sort_values(["grade", "class", "number"]).iterrows():
                m = r['email']
                if st.session_state.edit_student_email == m:
                    with st.form(f"es_{m}"):
                        c1, c2, c3 = st.columns([2, 2, 2]); nln = c1.text_input("姓", value=r['last_name']); nfn = c1.text_input("名", value=r['first_name']); ng = c2.text_input("学年", value=r['grade']); nc = c2.text_input("組", value=r['class']); nn = c3.text_input("番", value=r['number'])
                        if st.form_submit_button("保存"): supabase.table("students").update({"last_name":nln, "first_name":nfn, "grade":to_hankaku(ng), "class":fix_class_name(nc), "number":to_hankaku(nn)}).eq("email", m).execute(); st.session_state.edit_student_email = None; st.rerun()
                else:
                    cols = st.columns([1.5, 0.8, 0.8, 0.8, 1, 1]); cols[0].write(f"{r['last_name']} {r['first_name']}"); cols[1].write(r["grade"]); cols[2].write(r["class"]); cols[3].write(r["number"])
                    if cols[4].button("編集", key=f"ese_{m}"): st.session_state.edit_student_email = m; st.rerun()
                    if cols[5].button("削除", key=f"esd_{m}"): supabase.table("students").delete().eq("email", m).execute(); st.rerun()

    # --- 📖 授業管理 ---
    elif sel_menu == m_course:
        st.header(m_course)
        sub_areas = ["国語", "社会", "数学", "理科", "英語", "保健体育", "芸術", "家庭科", "情報", "学校設定", "探究", "特別活動"]
        
        if st.session_state.selected_course:
            # 【詳細画面】
            cid = st.session_state.selected_course
            if st.button("← 戻る"): st.session_state.selected_course = None; st.rerun()
            crs_res = supabase.table("courses_info").select("*").eq("id", cid).execute()
            if crs_res.data:
                crs = crs_res.data[0]
                st.subheader(f"📖 【{crs['subject_area']}】 {crs['name']} (担当: {crs['teacher_name']})")
                b_cols = st.columns(4)
                if b_cols[0].button("削除"): supabase.table("courses_info").delete().eq("id", cid).execute(); st.session_state.selected_course = None; st.rerun()
                
                # 履修生徒一覧
                s_en = supabase.table("user_courses").select("students(class, number, last_name, first_name)").eq("course_id", cid).execute()
                df_en = pd.DataFrame([{"クラス":r['students']['class'], "番号":r['students']['number'], "氏名":f"{r['students']['last_name']} {r['students']['first_name']}"} for r in s_en.data if r['students']])
                if not df_en.empty: st.dataframe(df_en.sort_values(["クラス", "番号"]), hide_index=True, use_container_width=True)
        else:
            # 【一覧と新規登録のタブ】
            t1, t2 = st.tabs(["授業一覧", "新規登録"])
            with t1:
                c_res = supabase.table("courses_info").select("*").order("subject_area").execute()
                df_c = pd.DataFrame(c_res.data)
                if not df_c.empty:
                    for sa in sorted(df_c['subject_area'].unique()):
                        st.write(f"#### {sa}")
                        gc = df_c[df_c['subject_area'] == sa]
                        cols = st.columns(4)
                        for i, (_, r) in enumerate(gc.iterrows()):
                            with cols[i%4]:
                                with st.container(border=True):
                                    st.write(f"**{r['name']}**\n\n{r['teacher_name']}")
                                    if st.button("詳細", key=f"cb_{r['id']}", use_container_width=True): 
                                        st.session_state.selected_course = r['id']; st.rerun()
                else: st.info("登録されている授業はありません。")

            with t2:
               with st.form("nc"):
                    cs = st.selectbox("教科", sub_areas)
                    cn = st.text_input("科目名")
                    # 複数選択できるように multiselect に変更
                    ct_list = st.multiselect("担当教員 (1名以上選択)", [t for t in teacher_options if t != "なし"])
                    
                    if st.form_submit_button("登録"):
                        if cn and ct_list:
                            # 選択された教員名をカンマ区切りで保存
                            supabase.table("courses_info").insert({
                                "subject_area": cs, 
                                "name": cn, 
                                "teacher_name": ", ".join(ct_list)
                            }).execute()
                            st.rerun()
                        elif not ct_list:
                            st.error("担当教員を1名以上選択してください。")
    # --- 👨‍🏫 教員管理 ---
    elif sel_menu == m_teacher:
        st.header(m_teacher)
        t1, t2, t3 = st.tabs(["一覧・削除", "個別登録", "CSV登録"])
        with t1:
            t_res = supabase.table("admins").select("*").execute()
            df_t = pd.DataFrame(t_res.data)
            if not df_t.empty:
                h_cols = st.columns([2, 2, 2, 1, 1]); h_cols[0].write("**名前**"); h_cols[1].write("**フリガナ**"); h_cols[2].write("**教科**"); st.divider()
                for _, r in df_t.iterrows():
                    m = r['email']
                    if st.session_state.edit_email == m:
                        with st.form(f"et_{m}"):
                            c1, c2, c3 = st.columns(3)
                            ln = c1.text_input("姓", value=r['last_name']); fn = c1.text_input("名", value=r['first_name'])
                            lnf = c1.text_input("姓フリ", value=r['last_name_furi']); fnf = c2.text_input("名フリ", value=r['first_name_furi'])
                            nm = c2.text_input("メールアドレス", value=r['email'])
                            sub = c3.text_input("教科", value=r['subject'])
                            if st.form_submit_button("更新"):
                                supabase.table("admins").update({"last_name":ln, "first_name":fn, "last_name_furi":lnf, "first_name_furi":fnf, "email":to_hankaku(nm).lower(), "subject":sub}).eq("email", m).execute()
                                st.session_state.edit_email = None; st.rerun()
                    else:
                        cols = st.columns([2, 2, 2, 1, 1]); cols[0].write(f"{r['last_name']} {r['first_name']}"); cols[1].write(f"{r['last_name_furi']} {r['first_name_furi']}"); cols[2].write(r['subject'])
                        if cols[3].button("編集", key=f"bt_{m}"): st.session_state.edit_email = m; st.rerun()
                        if cols[4].button("削除", key=f"dt_{m}"):
                            if m != SUPER_ADMIN: supabase.table("admins").delete().eq("email", m).execute(); st.rerun()
        with t2:
            with st.form("t_reg"):
                c1, c2 = st.columns(2); ln = c1.text_input("姓"); fn = c2.text_input("名"); lnf = c1.text_input("姓フリ"); fnf = c2.text_input("名フリ"); mail = st.text_input("メアド"); sub = st.text_input("担当教科")
                if st.form_submit_button("登録"): supabase.table("admins").upsert({"email":to_hankaku(mail).lower(), "last_name":ln, "first_name":fn, "last_name_furi":lnf, "first_name_furi":fnf, "subject":sub}).execute(); st.rerun()
        with t3:
            st.download_button("📥 テンプレート", data=get_csv_template(["メールアドレス", "姓", "名", "姓フリガナ", "名フリガナ", "担当教科"]), file_name="t_temp.csv")
            up = st.file_uploader("教員CSVを選択", type="csv")
            if up and st.button("一斉登録"):
                df = pd.read_csv(io.BytesIO(up.read())); [supabase.table("admins").upsert({"email":to_hankaku(str(r[0])).lower(), "last_name":str(r[1]), "first_name":str(r[2]), "last_name_furi":str(r[3]), "first_name_furi":str(r[4]), "subject":str(r[5])}).execute() for _, r in df.iterrows()]; st.rerun()
else:
    st.info("サイドバーにログイン情報を入力してください。")




