# صندوق شركة عزم الشرق — النسخة المركزية الدائمة

أول دخول:
- Username: admin
- Password: Azm@2026

مهم جداً:
في الإنتاج اضبط Environment Variable باسم DATABASE_URL على رابط PostgreSQL دائم.
إذا لم يوجد DATABASE_URL سيستخدم البرنامج SQLite محلياً للاختبار فقط.

بعد ضبط PostgreSQL:
- كل الأجهزة تشاهد نفس البيانات.
- البيانات تبقى بعد Deploy/Restart.
- المستخدمون والصلاحيات مشتركة.
- الرصيد USD/IQD مركزي.
