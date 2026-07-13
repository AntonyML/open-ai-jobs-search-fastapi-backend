with open(r'app/services/upskill.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    upskill = Upskill(
        user_id=user_id,
        candidate_id=candidate.id,
        target_job_posting_id=target_job_posting_id,
        target_job_url=target_job_url,
        status="running",
    )'''

new = '''    upskill = Upskill(
        user_id=user_id,
        candidate_id=candidate.id,
        mode=mode,
        target_job_posting_id=target_job_posting_id,
        target_job_url=target_job_url,
        status="running",
    )'''

if old in content:
    content = content.replace(old, new)
    with open(r'app/services/upskill.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed upskill service')
else:
    print('Pattern not found')
    idx = content.find('upskill = Upskill(')
    if idx != -1:
        print(repr(content[idx-100:idx+300]))
