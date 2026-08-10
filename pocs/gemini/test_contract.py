from run import validate_segment
def test_schema_segment(): assert validate_segment({'id':'s1','text':'Xin chào'})
def test_rejects_missing_id_or_unsafe_text(): assert not validate_segment({'text':'x'}); assert not validate_segment({'id':'s1','text':'unsafe text'})
