// Argus 本地开发 Jenkins 初始化：创建 admin 管理用户（幂等），清理旧演示用户 argus。
// API Token 在启动后经 UI 端点生成（见 deploy/README.md 的「本地 Jenkins」小节）。
import jenkins.model.*
import hudson.model.*
import hudson.security.*

def jenkins = Jenkins.get()
def password = System.getenv('ARGUS_JENKINS_PASSWORD') ?: 'admin'
if (!(jenkins.getSecurityRealm() instanceof HudsonPrivateSecurityRealm)) {
    def realm = new HudsonPrivateSecurityRealm(false)
    realm.createAccount('admin', password)
    jenkins.setSecurityRealm(realm)
}
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
jenkins.setAuthorizationStrategy(strategy)
if (jenkins.getNumExecutors() < 6) {
    jenkins.setNumExecutors(6)
}
jenkins.save()

if (User.getById('admin', false) == null) {
    // 安全域已存在但尚无 admin（如从旧版初始化迁移过来）：补建后需重启一次生效密码。
    def realm = new HudsonPrivateSecurityRealm(false)
    realm.createAccount('admin', password)
    jenkins.setSecurityRealm(realm)
    jenkins.save()
    println 'ARGUS-JENKINS-INIT: admin user created (restart once if realm already existed)'
}
def legacy = User.getById('argus', false)
if (legacy != null) {
    legacy.delete()
    println 'ARGUS-JENKINS-INIT: legacy user argus removed'
}
println 'ARGUS-JENKINS-INIT: security realm ready (admin)'
