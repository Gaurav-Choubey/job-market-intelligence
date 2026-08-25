USE analytics_project;

SHOW TABLES;
SELECT COUNT(*) AS total_jobs
FROM jobs_clean;
SELECT COUNT(*) AS total_skill_records
FROM job_skills;
SELECT *
FROM jobs_clean
LIMIT 5;
SELECT *
FROM job_skills
LIMIT 10;