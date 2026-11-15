"""Add this read-only route to admin-system/backend/routes/teaching.py.

The plugin requests:
    GET /api/public/teaching/timeline
It only exposes the published class time slots, not device or student data.
"""


@teaching_bp.route('/api/public/teaching/timeline', methods=['GET'])
def public_timeline():
    grade = request.args.get('grade', '').strip()
    class_name = request.args.get('class_name', '').strip()
    with policy_db() as conn:
        now = int(time.time())
        row = conn.execute(
            'SELECT revision, document_json FROM teaching_releases '
            'WHERE valid_from<=? AND usable_until>? ORDER BY revision DESC LIMIT 1',
            (now, now),
        ).fetchone()
        if row:
            revision = row['revision']
            document = json.loads(row['document_json'])
        else:
            revision = 0
            document = default_document()

        class_id = None
        class_grade = None
        if class_name:
            cls = conn.execute(
                'SELECT id, class_grade FROM classes '
                'WHERE class_format_name=? OR class_name=? LIMIT 1',
                (class_name, class_name),
            ).fetchone()
            if cls:
                class_id = cls['id']
                class_grade = cls['class_grade']
        if grade and class_grade is None:
            try:
                class_grade = int(grade)
            except ValueError:
                pass

        applicable = []
        for schedule in document.get('schedules') or []:
            if not isinstance(schedule, dict):
                continue
            scope = schedule.get('scopeType')
            scope_id = str(schedule.get('scopeId') or '')
            if scope == 'school':
                applicable.append(schedule)
            elif scope == 'grade' and class_grade is not None and scope_id == str(class_grade):
                applicable.append(schedule)
            elif scope == 'class' and class_id is not None and scope_id == str(class_id):
                applicable.append(schedule)
        if not applicable:
            return jsonify(code=1, msg='当前没有可用课表'), 404

        selected = max(
            applicable,
            key=lambda item: {'school': 0, 'grade': 1, 'class': 2}.get(item.get('scopeType'), 0),
        )
        return jsonify(code=0, data={
            'revision': revision,
            'timezone': document.get('timezone', 'Asia/Shanghai'),
            'termId': document.get('termId', ''),
            'schedule': selected,
        })
