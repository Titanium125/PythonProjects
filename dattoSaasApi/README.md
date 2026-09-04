This script is designed to be used with the Datto SaaS mailbox protection. It works with both M365 and Google Workspace mailboxes. 

Add your Datto api keys as strings in the .env file. 

You will also want to review the imports at the top of each file to ensure you have what you need, but you'll likely need to install requests, json, and dotenv.
I recommend using pip for this, but that's up to you. 

Script has 2 main functions. Purging of pasued mailboxes, turning active mailboxes into paused ones. I put this together because the Datto GUI sucks. There is no way to edit mailboxes in large quantities, so this script is needed.

Datto seems to have an API limitation of 100 at a time, so if you were to run on operation on 101 accounts at once, only 100 of them would actually be affected. 

The script makes an API call to datto to get all your organizations attributes and stores them in a domain.json file. If you only have 1 org then it will only have one entry. 
Then the script takes the primary domain name as input. 
Then it pulls all users from the API along with their states. Datto Web GUI has a organization unit filter you can use for users, but this does not exist in the API. Meaning we have to make edits one at a time.
The script can import a csv file of all accounts to disable, and it will loop through multiple times if you do more than 100 accounts. Loop behavior is Total number of accounts // 100 + 1 loops. So 701 accounts requires 8 loops to fully apply. 

Datto sucks so if you do a full CSV upload or use the built in picker list that enumerates 20 accounts a time, you are still hand selecting every one. Datto doesn't have an easy way to only mess with the ones you want, hence this script. 
You can export all users in Datto to a CSV, but you can't apply filters of any kind. So again you have to go through the CSV hand selecting each one you want. It's still faster than going through the GUI and waiting on the slow ass front end though. 

Script first turns active accounts into paused accounts. You can then purge all paused accounts moving them to unlicensed. After 30 days this data is deleted. 